"""Persistent Q&A history with user-defined groups and citation revalidation."""

from __future__ import annotations

import re
import uuid
from typing import Any

from .config import RuntimeSettings
from .db import Database, json_dumps, json_loads
from .utils import iso, stable_id
from .vault import Vault


class QAHistoryService:
    def __init__(self, db: Database, vault: Vault, settings: RuntimeSettings) -> None:
        self.db = db
        self.vault = vault
        self.settings = settings

    def save(self, question: str, result: dict[str, Any], group_id: str | None = None) -> str | None:
        if not self.settings.get_bool("qa_history_enabled"):
            return None
        if group_id and not self._group_exists(group_id):
            group_id = None
        entry_id = stable_id("qa", uuid.uuid4().hex)
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO qa_history(id, group_id, question, answer, mode, grounded,
                                       citations_json, evidence_json, notice, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry_id,
                    group_id,
                    question[:4000],
                    str(result.get("answer") or "")[:20000],
                    str(result.get("mode") or "unknown"),
                    int(bool(result.get("grounded"))),
                    json_dumps(result.get("citations") or []),
                    json_dumps(result.get("evidence") or []),
                    result.get("notice"),
                    iso(),
                ),
            )
        return entry_id

    def groups(self) -> dict[str, Any]:
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT g.id, g.name, COUNT(h.id) AS count
                FROM qa_groups g
                LEFT JOIN qa_history h ON h.group_id = g.id
                GROUP BY g.id
                ORDER BY g.name COLLATE NOCASE
                """
            ).fetchall()
            ungrouped = conn.execute(
                "SELECT COUNT(*) AS n FROM qa_history WHERE group_id IS NULL"
            ).fetchone()["n"]
            total = conn.execute("SELECT COUNT(*) AS n FROM qa_history").fetchone()["n"]
        return {
            "groups": [dict(row) for row in rows],
            "ungrouped_count": ungrouped,
            "total": total,
        }

    def create_group(self, name: str) -> dict[str, Any]:
        name = re.sub(r"\s+", " ", name or "").strip()[:80]
        if not name:
            raise ValueError("Group name is empty")
        group_id = stable_id("qag", name.lower())
        with self.db.connect() as conn:
            existing = conn.execute("SELECT * FROM qa_groups WHERE name = ?", (name,)).fetchone()
            if existing:
                return dict(existing)
            conn.execute(
                "INSERT INTO qa_groups(id, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (group_id, name, iso(), iso()),
            )
            row = conn.execute("SELECT * FROM qa_groups WHERE id = ?", (group_id,)).fetchone()
        return dict(row)

    def rename_group(self, group_id: str, name: str) -> dict[str, Any]:
        name = re.sub(r"\s+", " ", name or "").strip()[:80]
        if not name:
            raise ValueError("Group name is empty")
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM qa_groups WHERE id = ?", (group_id,)).fetchone()
            if row is None:
                raise KeyError(group_id)
            duplicate = conn.execute(
                "SELECT id FROM qa_groups WHERE name = ? AND id <> ?", (name, group_id)
            ).fetchone()
            if duplicate:
                raise ValueError("Group name already exists")
            conn.execute(
                "UPDATE qa_groups SET name = ?, updated_at = ? WHERE id = ?",
                (name, iso(), group_id),
            )
            row = conn.execute("SELECT * FROM qa_groups WHERE id = ?", (group_id,)).fetchone()
        return dict(row)

    def delete_group(self, group_id: str) -> None:
        with self.db.connect() as conn:
            conn.execute("UPDATE qa_history SET group_id = NULL WHERE group_id = ?", (group_id,))
            conn.execute("DELETE FROM qa_groups WHERE id = ?", (group_id,))

    def list(
        self,
        *,
        group_id: str | None = None,
        ungrouped: bool = False,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if ungrouped:
            clauses.append("h.group_id IS NULL")
        elif group_id:
            clauses.append("h.group_id = ?")
            params.append(group_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(limit, 300)))
        with self.db.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT h.*, g.name AS group_name
                FROM qa_history h
                LEFT JOIN qa_groups g ON g.id = h.group_id
                {where}
                ORDER BY h.created_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        result = []
        for row in rows:
            citations = json_loads(row["citations_json"], [])
            answer = row["answer"] or ""
            result.append(
                {
                    "id": row["id"],
                    "group_id": row["group_id"],
                    "group_name": row["group_name"],
                    "question": row["question"],
                    "answer_preview": answer[:180],
                    "mode": row["mode"],
                    "grounded": bool(row["grounded"]),
                    "citation_count": len(citations),
                    "created_at": row["created_at"],
                }
            )
        return result

    def get(self, entry_id: str) -> dict[str, Any]:
        with self.db.connect() as conn:
            row = conn.execute(
                """
                SELECT h.*, g.name AS group_name
                FROM qa_history h
                LEFT JOIN qa_groups g ON g.id = h.group_id
                WHERE h.id = ?
                """,
                (entry_id,),
            ).fetchone()
        if row is None:
            raise KeyError(entry_id)
        citations = self._resolve_items(json_loads(row["citations_json"], []))
        evidence = self._resolve_items(json_loads(row["evidence_json"], []))
        return {
            "id": row["id"],
            "group_id": row["group_id"],
            "group_name": row["group_name"],
            "question": row["question"],
            "answer": row["answer"],
            "mode": row["mode"],
            "grounded": bool(row["grounded"]),
            "citations": citations,
            "evidence": evidence,
            "notice": row["notice"],
            "created_at": row["created_at"],
            "local_note_count": len({item.get("note_id") for item in citations if item.get("note_id")}),
        }

    def assign(self, entry_id: str, group_id: str | None) -> dict[str, Any]:
        if group_id and not self._group_exists(group_id):
            raise KeyError(group_id)
        with self.db.connect() as conn:
            cursor = conn.execute(
                "UPDATE qa_history SET group_id = ? WHERE id = ?", (group_id, entry_id)
            )
            if cursor.rowcount == 0:
                raise KeyError(entry_id)
        return self.get(entry_id)

    def delete(self, entry_id: str) -> None:
        with self.db.connect() as conn:
            conn.execute("DELETE FROM qa_history WHERE id = ?", (entry_id,))

    def clear(self) -> None:
        with self.db.connect() as conn:
            conn.execute("DELETE FROM qa_history")

    def _group_exists(self, group_id: str) -> bool:
        with self.db.connect() as conn:
            row = conn.execute("SELECT 1 FROM qa_groups WHERE id = ?", (group_id,)).fetchone()
        return row is not None

    def _resolve_items(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        resolved = []
        for item in items:
            if not isinstance(item, dict):
                continue
            entry = dict(item)
            note_id = entry.get("note_id")
            note = self.vault.read_note(note_id) if note_id else None
            if note is None:
                entry["stale"] = True
                entry["app_url"] = None
                entry["obsidian_uri"] = None
            else:
                entry["stale"] = False
                entry["path"] = note["path"]
                entry["app_url"] = f"/api/notes/{note_id}"
                entry["obsidian_uri"] = note["obsidian_uri"]
                entry["title"] = entry.get("title") or note["title"]
            resolved.append(entry)
        return resolved
