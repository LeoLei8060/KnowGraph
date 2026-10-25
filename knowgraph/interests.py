"""Short-lived, explainable interests derived only from active user queries."""

from __future__ import annotations

import math
from datetime import timedelta
from typing import Any

from .config import RuntimeSettings
from .db import Database, json_dumps, json_loads
from .tags import TagService, extract_topics
from .utils import iso, parse_iso, utcnow

KIND_WEIGHTS = {
    "search": 1.0,
    "ask": 1.35,
    "open": 0.25,
    "save": 1.4,
    "not_relevant": -1.2,
    "block": -2.0,
}


class InterestService:
    def __init__(self, db: Database, settings: RuntimeSettings, tags: TagService) -> None:
        self.db = db
        self.settings = settings
        self.tags = tags

    def record_interaction(
        self,
        kind: str,
        query: str,
        *,
        item_id: str | None = None,
        note_id: str | None = None,
        meta: dict[str, Any] | None = None,
        topics: list[dict[str, str]] | None = None,
    ) -> list[dict[str, str]]:
        if not self.settings.get_bool("track_interactions"):
            return []
        resolved = topics if topics is not None else extract_topics(query or "", self.tags.aliases())
        with self.db.connect() as conn:
            conn.execute(
                "INSERT INTO interactions(kind, query, item_id, note_id, topics_json, meta_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    kind,
                    (query or "")[:1000],
                    item_id,
                    note_id,
                    json_dumps(resolved),
                    json_dumps(meta or {}),
                    iso(),
                ),
            )
        self.recompute()
        return resolved

    def recompute(self, now=None) -> list[dict[str, Any]]:
        now = now or utcnow()
        half_life = max(1.0, self.settings.get_int("interest_half_life_days", 14))
        expiry_days = max(1, self.settings.get_int("interest_expiry_days", 45))
        cutoff = now - timedelta(days=expiry_days)
        scores: dict[str, float] = {}
        labels: dict[str, str] = {}
        signals: dict[str, int] = {}
        last_seen: dict[str, str] = {}
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM interactions WHERE excluded = 0 AND created_at >= ? ORDER BY created_at",
                (iso(cutoff),),
            ).fetchall()
            for row in rows:
                created = parse_iso(row["created_at"])
                if created is None:
                    continue
                age = max(0.0, (now - created).total_seconds() / 86400)
                decay = 0.5 ** (age / half_life)
                weight = KIND_WEIGHTS.get(row["kind"], 0.5) * decay
                for topic in json_loads(row["topics_json"], []):
                    slug = str(topic.get("slug") or "").strip()
                    if not slug:
                        continue
                    scores[slug] = scores.get(slug, 0.0) + weight
                    labels.setdefault(slug, str(topic.get("label") or slug))
                    signals[slug] = signals.get(slug, 0) + 1
                    last_seen[slug] = row["created_at"]

            existing = {
                row["slug"]: row for row in conn.execute("SELECT * FROM interests").fetchall()
            }
            for slug in set(existing) | set(scores):
                old = existing.get(slug)
                pinned = bool(old["pinned"]) if old else False
                blocked = bool(old["blocked"]) if old else False
                score = max(0.0, scores.get(slug, 0.0))
                if score < 0.05 and not pinned and not blocked:
                    conn.execute("DELETE FROM interests WHERE slug = ?", (slug,))
                    continue
                if blocked:
                    score = 0.0
                conn.execute(
                    """
                    INSERT INTO interests(slug, label, score, signals, last_signal_at, pinned, blocked, source, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'auto', ?)
                    ON CONFLICT(slug) DO UPDATE SET
                        label = excluded.label, score = excluded.score, signals = excluded.signals,
                        last_signal_at = excluded.last_signal_at, updated_at = excluded.updated_at
                    """,
                    (
                        slug,
                        labels.get(slug) or (old["label"] if old else slug),
                        score,
                        signals.get(slug, 0),
                        last_seen.get(slug) or (old["last_signal_at"] if old else None),
                        int(pinned),
                        int(blocked),
                        iso(now),
                    ),
                )
        return self.list()

    def list(self, include_blocked: bool = True) -> list[dict[str, Any]]:
        expiry_days = self.settings.get_int("interest_expiry_days", 45)
        with self.db.connect() as conn:
            if include_blocked:
                rows = conn.execute(
                    "SELECT * FROM interests ORDER BY blocked ASC, pinned DESC, score DESC"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM interests WHERE blocked = 0 ORDER BY pinned DESC, score DESC"
                ).fetchall()
        output = []
        for row in rows:
            last = parse_iso(row["last_signal_at"])
            expires = iso(last + timedelta(days=expiry_days)) if last else None
            output.append(
                {
                    "slug": row["slug"],
                    "label": row["label"],
                    "score": round(row["score"], 3),
                    "signals": row["signals"],
                    "last_signal_at": row["last_signal_at"],
                    "expires_at": expires,
                    "pinned": bool(row["pinned"]),
                    "blocked": bool(row["blocked"]),
                    "source": row["source"],
                }
            )
        return output

    def top(self, limit: int = 5) -> list[dict[str, Any]]:
        return [
            item
            for item in self.list(include_blocked=False)
            if item["pinned"] or item["score"] >= 0.3
        ][:limit]

    def labels(self) -> dict[str, str]:
        return {item["slug"]: item["label"] for item in self.list()}

    def set_pinned(self, slug: str, pinned: bool) -> dict[str, Any]:
        return self._set_flag(slug, "pinned", pinned)

    def set_blocked(self, slug: str, blocked: bool) -> dict[str, Any]:
        return self._set_flag(slug, "blocked", blocked)

    def _set_flag(self, slug: str, field: str, value: bool) -> dict[str, Any]:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM interests WHERE slug = ?", (slug,)).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO interests(slug, label, score, signals, pinned, blocked, source, updated_at) "
                    "VALUES (?, ?, 0, 0, ?, ?, 'manual', ?)",
                    (slug, slug, int(field == "pinned" and value), int(field == "blocked" and value), iso()),
                )
            else:
                conn.execute(
                    f"UPDATE interests SET {field} = ?, source = 'manual', updated_at = ? WHERE slug = ?",
                    (int(value), iso(), slug),
                )
        if field == "blocked" and value:
            with self.db.connect() as conn:
                conn.execute("UPDATE interests SET score = 0 WHERE slug = ?", (slug,))
        self.recompute()
        return {"slug": slug, field: value}

    def delete_interest(self, slug: str, *, clear_events: bool = True) -> None:
        with self.db.connect() as conn:
            if clear_events:
                rows = conn.execute("SELECT id, topics_json FROM interactions").fetchall()
                for row in rows:
                    topics = json_loads(row["topics_json"], [])
                    if any(topic.get("slug") == slug for topic in topics):
                        conn.execute("DELETE FROM interactions WHERE id = ?", (row["id"],))
            conn.execute("DELETE FROM interests WHERE slug = ?", (slug,))
        self.recompute()

    def clear_history(self) -> None:
        with self.db.connect() as conn:
            conn.execute("DELETE FROM interactions")
            conn.execute(
                "DELETE FROM interests WHERE source = 'auto' AND pinned = 0 AND blocked = 0"
            )
        self.recompute()

    def evidence_for(self, slug: str, limit: int = 5) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT kind, query, topics_json, created_at FROM interactions WHERE excluded = 0 "
                "ORDER BY created_at DESC LIMIT 300"
            ).fetchall()
        output = []
        for row in rows:
            topics = json_loads(row["topics_json"], [])
            if any(topic.get("slug") == slug for topic in topics):
                output.append({"kind": row["kind"], "query": row["query"], "created_at": row["created_at"]})
                if len(output) >= limit:
                    break
        return output


def decayed_score(raw_score: float, last_signal: str | None, half_life_days: float, now=None) -> float:
    last = parse_iso(last_signal)
    if last is None:
        return 0.0
    now = now or utcnow()
    age = max(0.0, (now - last).total_seconds() / 86400)
    return raw_score * math.pow(0.5, age / max(1.0, half_life_days))
