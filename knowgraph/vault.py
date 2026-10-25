"""Markdown vault writes with stable IDs, atomic replacement and edit protection."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote

from .db import Database, json_dumps
from .tags import normalize_tag
from .utils import (
    filename_slug,
    iso,
    parse_iso,
    relative_to,
    render_markdown,
    sha256_bytes,
    sha256_text,
    split_frontmatter,
    stable_id,
    utcnow,
)

SUBDIRS = ["00-Inbox", "10-Sources", "20-Notes", "30-Maps", "40-Practice", "50-Reviews", "Attachments"]
KIND_DIRS = {
    "source": "00-Inbox",
    "note": "20-Notes",
    "review": "50-Reviews",
    "practice": "40-Practice",
    "map": "30-Maps",
}


class VaultConflict(RuntimeError):
    pass


class Vault:
    def __init__(self, root: Path, db: Database) -> None:
        self.root = Path(root).resolve()
        self.db = db
        self.ensure()

    def ensure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        for name in SUBDIRS:
            (self.root / name).mkdir(parents=True, exist_ok=True)

    def safe_path(self, relative: str) -> Path:
        candidate = (self.root / relative).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise VaultConflict(f"Path escapes the vault: {relative}") from exc
        return candidate

    def _row(self, note_id: str) -> Any:
        with self.db.connect() as conn:
            return conn.execute("SELECT * FROM note_index WHERE note_id = ?", (note_id,)).fetchone()

    def note_path(self, note_id: str) -> Path | None:
        row = self._row(note_id)
        if row is None:
            return None
        return self.safe_path(row["path"])

    def obsidian_uri(self, note_id: str) -> str | None:
        path = self.note_path(note_id)
        if path is None:
            return None
        return "obsidian://open?path=" + quote(str(path))

    def _atomic_write(self, target: Path, content: str) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="\n",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        )
        temp_path = Path(handle.name)
        try:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            handle.close()
            os.replace(temp_path, target)
        except Exception:
            handle.close()
            temp_path.unlink(missing_ok=True)
            raise

    def write_note(
        self,
        *,
        note_id: str,
        kind: str,
        title: str,
        body: str,
        frontmatter: dict[str, Any] | None = None,
        subdir: str | None = None,
        status: str = "",
        tags: Iterable[str] = (),
        on_conflict: str = "revision",
    ) -> dict[str, Any]:
        """Write a managed note without silently overwriting manual edits.

        A conflicting rewrite is stored as a new revision whose frontmatter
        records ``supersedes``; the edited file is left untouched.
        """
        subdir = subdir or KIND_DIRS.get(kind, "20-Notes")
        if subdir not in SUBDIRS:
            raise VaultConflict(f"Unsupported vault folder: {subdir}")
        existing = self._row(note_id)
        slug = filename_slug(title)
        short = note_id.replace("_", "-")[-12:]
        relative = existing["path"] if existing else f"{subdir}/{slug}--{short}.md"
        target = self.safe_path(relative)
        managed_hash = existing["managed_hash"] if existing else None
        conflict = False
        if target.exists():
            disk_hash = sha256_bytes(target.read_bytes())
            conflict = managed_hash is None or disk_hash != managed_hash
        if conflict:
            if on_conflict == "skip":
                return {
                    "note_id": note_id,
                    "path": relative,
                    "conflict": True,
                    "revision": False,
                    "written": False,
                }
            revision = 2
            while True:
                revision_id = f"{note_id}-r{revision}"
                revision_rel = f"{subdir}/{slug}--{short}-r{revision}.md"
                if not self.safe_path(revision_rel).exists():
                    break
                revision += 1
            frontmatter = dict(frontmatter or {})
            frontmatter["supersedes"] = note_id
            note_id = revision_id
            relative = revision_rel
            target = self.safe_path(relative)

        now = iso()
        meta: dict[str, Any] = dict(frontmatter or {})
        meta.update(
            {
                "id": note_id,
                "kind": kind,
                "status": status,
                "title": title,
                "tags": [t for t in tags if t],
                "updated": now[:10],
            }
        )
        if "created" not in meta:
            meta["created"] = now[:10]
        content = render_markdown(meta, body)
        self._atomic_write(target, content)
        self._index(
            note_id=note_id,
            relative=relative,
            title=title,
            kind=kind,
            status=status,
            body=body,
            tags=list(tags),
            content=content,
        )
        return {
            "note_id": note_id,
            "path": relative,
            "conflict": conflict,
            "revision": conflict,
            "written": True,
        }

    def _index(
        self,
        *,
        note_id: str,
        relative: str,
        title: str,
        kind: str,
        status: str,
        body: str,
        tags: Iterable[str],
        content: str,
    ) -> None:
        normalized = []
        for raw in tags:
            slug = normalize_tag(str(raw))
            if slug and slug not in normalized:
                normalized.append(slug)
        content_hash = sha256_text(content)
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO note_index(note_id, path, title, kind, status, body, tags_json,
                                       content_hash, managed_hash, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(note_id) DO UPDATE SET
                    path = excluded.path,
                    title = excluded.title,
                    kind = excluded.kind,
                    status = excluded.status,
                    body = excluded.body,
                    tags_json = excluded.tags_json,
                    content_hash = excluded.content_hash,
                    managed_hash = excluded.managed_hash,
                    updated_at = excluded.updated_at
                """,
                (
                    note_id,
                    relative,
                    title,
                    kind,
                    status,
                    body,
                    json_dumps(normalized),
                    content_hash,
                    content_hash,
                    iso(),
                ),
            )
            conn.execute("DELETE FROM note_tags WHERE note_id = ?", (note_id,))
            conn.executemany(
                "INSERT OR IGNORE INTO note_tags(note_id, tag) VALUES (?, ?)",
                [(note_id, tag) for tag in normalized],
            )
            conn.execute("DELETE FROM note_fts WHERE note_id = ?", (note_id,))
            conn.execute(
                "INSERT INTO note_fts(note_id, title, body, tags) VALUES (?, ?, ?, ?)",
                (note_id, title, body, " ".join(normalized)),
            )

    def read_note(self, note_id: str) -> dict[str, Any] | None:
        row = self._row(note_id)
        if row is None:
            return None
        path = self.safe_path(row["path"])
        if not path.exists():
            return None
        raw = path.read_text(encoding="utf-8")
        meta, body = split_frontmatter(raw)
        return {
            "note_id": note_id,
            "path": row["path"],
            "absolute_path": str(path),
            "title": row["title"],
            "kind": row["kind"],
            "status": row["status"],
            "tags": normalize_tags(meta.get("tags") or []),
            "body": body,
            "content": raw,
            "obsidian_uri": "obsidian://open?path=" + quote(str(path)),
        }

    def remove_note_file(self, note_id: str) -> None:
        row = self._row(note_id)
        if row is None:
            return
        path = self.safe_path(row["path"])
        path.unlink(missing_ok=True)
        with self.db.connect() as conn:
            conn.execute("DELETE FROM note_fts WHERE note_id = ?", (note_id,))
            conn.execute("DELETE FROM note_tags WHERE note_id = ?", (note_id,))
            conn.execute("DELETE FROM note_index WHERE note_id = ?", (note_id,))

    def sync_from_disk(self) -> dict[str, int]:
        """Index externally edited Markdown, including files created in Obsidian."""
        seen: set[str] = set()
        updated = 0
        skipped = 0
        files = sorted(self.root.rglob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
        for path in files:
            if any(part.startswith(".") for part in path.relative_to(self.root).parts):
                continue
            try:
                relative = relative_to(path, self.root)
                raw = path.read_text(encoding="utf-8")
            except (OSError, ValueError):
                skipped += 1
                continue
            meta, body = split_frontmatter(raw)
            note_id = str(meta.get("id") or "").strip()
            if not note_id:
                note_id = stable_id("note", relative)
            if note_id in seen:
                skipped += 1
                continue
            seen.add(note_id)
            title = str(meta.get("title") or _first_heading(body) or path.stem)
            kind = str(meta.get("kind") or "note")
            status = str(meta.get("status") or "")
            tags = normalize_tags(meta.get("tags") or [])
            with self.db.connect() as conn:
                existing = conn.execute(
                    "SELECT path, content_hash, managed_hash FROM note_index WHERE note_id = ?",
                    (note_id,),
                ).fetchone()
                if existing and existing["path"] != relative:
                    taken = conn.execute(
                        "SELECT note_id FROM note_index WHERE path = ?", (relative,)
                    ).fetchone()
                    if taken and taken["note_id"] != note_id:
                        skipped += 1
                        continue
                conn.execute(
                    """
                    INSERT INTO note_index(note_id, path, title, kind, status, body, tags_json,
                                           content_hash, managed_hash, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
                    ON CONFLICT(note_id) DO UPDATE SET
                        path = excluded.path,
                        title = excluded.title,
                        kind = excluded.kind,
                        status = excluded.status,
                        body = excluded.body,
                        tags_json = excluded.tags_json,
                        content_hash = excluded.content_hash,
                        updated_at = excluded.updated_at
                    """,
                    (
                        note_id,
                        relative,
                        title,
                        kind,
                        status,
                        body,
                        json_dumps(tags),
                        sha256_text(raw),
                        iso(),
                    ),
                )
                conn.execute("DELETE FROM note_tags WHERE note_id = ?", (note_id,))
                conn.executemany(
                    "INSERT OR IGNORE INTO note_tags(note_id, tag) VALUES (?, ?)",
                    [(note_id, tag) for tag in tags],
                )
                conn.execute("DELETE FROM note_fts WHERE note_id = ?", (note_id,))
                conn.execute(
                    "INSERT INTO note_fts(note_id, title, body, tags) VALUES (?, ?, ?, ?)",
                    (note_id, title, body, " ".join(tags)),
                )
                if existing is None or existing["content_hash"] != sha256_text(raw):
                    updated += 1
                if kind == "source":
                    item_id = meta.get("item_id")
                    raw_hash = sha256_text(raw)
                    if (
                        item_id
                        and status
                        and existing
                        and existing["managed_hash"] == raw_hash
                    ):
                        conn.execute(
                            "UPDATE items SET status = ?, updated_at = ? WHERE id = ?",
                            (status, iso(), str(item_id)),
                        )
        with self.db.connect() as conn:
            rows = conn.execute("SELECT note_id, path FROM note_index").fetchall()
            for row in rows:
                if row["note_id"] in seen:
                    continue
                if not self.safe_path(row["path"]).exists():
                    conn.execute("DELETE FROM note_fts WHERE note_id = ?", (row["note_id"],))
                    conn.execute("DELETE FROM note_tags WHERE note_id = ?", (row["note_id"],))
                    conn.execute("DELETE FROM note_index WHERE note_id = ?", (row["note_id"],))
        return {"indexed": len(seen), "updated": updated, "skipped": skipped}

    def move_note(self, note_id: str, subdir: str) -> dict[str, Any] | None:
        """Move an unedited managed note to another vault folder."""
        if subdir not in SUBDIRS:
            raise VaultConflict(f"Unsupported vault folder: {subdir}")
        row = self._row(note_id)
        if row is None:
            return None
        source = self.safe_path(row["path"])
        if not source.exists():
            return None
        if row["managed_hash"] and sha256_bytes(source.read_bytes()) != row["managed_hash"]:
            return {"note_id": note_id, "moved": False, "reason": "manual_edits"}
        target_rel = f"{subdir}/{source.name}"
        target = self.safe_path(target_rel)
        if target.exists():
            return {"note_id": note_id, "moved": False, "reason": "target_exists"}
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(target))
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE note_index SET path = ?, updated_at = ? WHERE note_id = ?",
                (target_rel, iso(), note_id),
            )
        return {"note_id": note_id, "moved": True, "path": target_rel}


def normalize_tags(tags: Any) -> list[str]:
    if isinstance(tags, str):
        tags = [t for t in tags.replace("#", " ").replace(",", " ").split() if t]
    if not isinstance(tags, (list, tuple, set)):
        return []
    result: list[str] = []
    for tag in tags:
        slug = normalize_tag(str(tag))
        if slug and slug not in result:
            result.append(slug)
    return result


def _first_heading(body: str) -> str:
    for line in body.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def note_age_days(note_id: str, db: Database) -> float | None:
    with db.connect() as conn:
        row = conn.execute("SELECT updated_at FROM note_index WHERE note_id = ?", (note_id,)).fetchone()
    if not row:
        return None
    updated = parse_iso(row["updated_at"])
    if not updated:
        return None
    return max(0.0, (utcnow() - updated).total_seconds() / 86400)
