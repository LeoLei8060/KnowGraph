"""Explicit learning records. Only these can support claims of skill progress."""

from __future__ import annotations

from typing import Any

from .db import Database
from .utils import iso, stable_id

STATUSES = {"done", "partial", "blocked", "planned"}


class ProgressService:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(
        self,
        title: str,
        *,
        skill: str = "",
        status: str = "done",
        minutes: int = 0,
        notes: str = "",
        occurred_at: str | None = None,
    ) -> dict[str, Any]:
        if not title.strip():
            raise ValueError("Progress title is empty")
        if status not in STATUSES:
            raise ValueError("Unsupported progress status")
        progress_id = stable_id("progress", f"{title}:{iso()}")
        now = iso()
        with self.db.connect() as conn:
            conn.execute(
                "INSERT INTO progress_logs(id, title, skill, status, minutes, notes, occurred_at, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    progress_id,
                    title.strip()[:200],
                    skill.strip()[:80],
                    status,
                    max(0, int(minutes)),
                    notes[:2000],
                    occurred_at or now,
                    now,
                ),
            )
            row = conn.execute("SELECT * FROM progress_logs WHERE id = ?", (progress_id,)).fetchone()
        return dict(row)

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM progress_logs ORDER BY occurred_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]

    def delete(self, progress_id: str) -> None:
        with self.db.connect() as conn:
            conn.execute("DELETE FROM progress_logs WHERE id = ?", (progress_id,))
