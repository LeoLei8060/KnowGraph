"""Persistent local scheduler. Jobs survive restarts; execution is idempotent."""

from __future__ import annotations

import threading
from datetime import datetime, timedelta
from typing import Any

from .config import RuntimeSettings
from .db import Database, json_dumps, json_loads
from .ingest import IngestService
from .reviews import ReviewService, period_bounds
from .utils import iso, parse_iso, stable_id, utcnow


class Scheduler:
    def __init__(
        self,
        db: Database,
        settings: RuntimeSettings,
        ingest: IngestService,
        reviews: ReviewService,
    ) -> None:
        self.db = db
        self.settings = settings
        self.ingest = ingest
        self.reviews = reviews
        self.lock = threading.Lock()

    def enqueue(self, kind: str, payload: dict[str, Any], dedupe_key: str, due_at: datetime | None = None) -> str | None:
        job_id = stable_id("job", dedupe_key)
        with self.db.connect() as conn:
            existing = conn.execute("SELECT id FROM jobs WHERE dedupe_key = ?", (dedupe_key,)).fetchone()
            if existing:
                return None
            conn.execute(
                "INSERT INTO jobs(id, kind, dedupe_key, due_at, payload_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (job_id, kind, dedupe_key, iso(due_at or utcnow()), json_dumps(payload), iso()),
            )
        return job_id

    def run_now(self, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            return self._execute(kind, payload)

    def tick(self) -> dict[str, Any]:
        recovered = self.recover_stale()
        seeded = self.seed()
        executed = []
        if self.lock.acquire(blocking=False):
            try:
                for job in self._due_jobs():
                    if not self._claim(job["id"]):
                        continue
                    try:
                        result = self._execute(job["kind"], json_loads(job["payload_json"], {}))
                        self._finish(job["id"], "done", None)
                        executed.append({"id": job["id"], "kind": job["kind"], "result": result})
                    except Exception as exc:
                        self._finish(job["id"], "error", str(exc)[:1000])
                        executed.append({"id": job["id"], "kind": job["kind"], "error": str(exc)})
            finally:
                self.lock.release()
        return {"recovered": recovered, "seeded": seeded, "executed": executed}

    def seed(self) -> int:
        seeded = 0
        now = utcnow()
        with self.db.connect() as conn:
            sources = conn.execute("SELECT * FROM sources WHERE enabled = 1").fetchall()
        for source in sources:
            interval = max(5, int(source["interval_minutes"])) * 60
            slot = int(now.timestamp() // interval)
            last = parse_iso(source["last_run_at"])
            due = last is None or now >= last + timedelta(seconds=interval)
            if not due:
                continue
            if self.enqueue(
                "collect",
                {"source_id": source["id"]},
                f"collect:{source['id']}:{slot}",
                due_at=now,
            ):
                seeded += 1
        seeded += self._seed_reviews(now)
        return seeded

    def _seed_reviews(self, now: datetime) -> int:
        tz = self.settings.timezone()
        local = now.astimezone(tz)
        seeded = 0
        week_day = self.settings.get_int("weekly_review_day", 1)
        week_hour = self.settings.get_int("weekly_review_hour", 8)
        delta = local.weekday() - week_day
        if 0 <= delta <= 1 and local.hour >= week_hour:
            start, end, label, _ = period_bounds("week", local.date(), tz, -1)
            if not self._review_exists("week", start, end):
                dedupe = f"review_auto:week:{start.date().isoformat()}"
                if self.enqueue("review", {"period_type": "week", "offset": -1}, dedupe, due_at=now):
                    seeded += 1
        month_day = self.settings.get_int("monthly_review_day", 1)
        month_hour = self.settings.get_int("monthly_review_hour", 8)
        if month_day <= local.day <= month_day + 1 and local.hour >= month_hour:
            start, end, label, _ = period_bounds("month", local.date(), tz, -1)
            if not self._review_exists("month", start, end):
                dedupe = f"review_auto:month:{start.date().isoformat()}"
                if self.enqueue("review", {"period_type": "month", "offset": -1}, dedupe, due_at=now):
                    seeded += 1
        return seeded

    def _review_exists(self, period_type: str, start: datetime, end: datetime) -> bool:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM reviews WHERE period_type = ? AND period_start = ? AND period_end = ? LIMIT 1",
                (period_type, iso(start), iso(end)),
            ).fetchone()
        return row is not None

    def _due_jobs(self) -> list[Any]:
        with self.db.connect() as conn:
            return conn.execute(
                "SELECT * FROM jobs WHERE status = 'pending' AND due_at <= ? ORDER BY due_at LIMIT 20",
                (iso(),),
            ).fetchall()

    def _claim(self, job_id: str) -> bool:
        with self.db.connect() as conn:
            cursor = conn.execute(
                "UPDATE jobs SET status = 'running', started_at = ? WHERE id = ? AND status = 'pending'",
                (iso(), job_id),
            )
            return cursor.rowcount == 1

    def _finish(self, job_id: str, status: str, error: str | None) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE jobs SET status = ?, finished_at = ?, error = ? WHERE id = ?",
                (status, iso(), error, job_id),
            )

    def _execute(self, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        if kind == "collect":
            source = self.ingest.sources.get(payload.get("source_id", ""))
            if source is None:
                raise KeyError("Source not found")
            return self.ingest.collect_source(source)
        if kind == "collect_all":
            return self.ingest.collect_all()
        if kind == "review":
            period_type = payload.get("period_type", "week")
            return self.reviews.generate(
                period_type,
                offset=int(payload.get("offset", -1)),
                force=bool(payload.get("force", False)),
            )
        if kind == "vault_sync":
            return self.ingest.vault.sync_from_disk()
        raise ValueError(f"Unknown job kind: {kind}")

    def recover_stale(self, minutes: int = 30) -> int:
        cutoff = utcnow() - timedelta(minutes=minutes)
        with self.db.connect() as conn:
            cursor = conn.execute(
                "UPDATE jobs SET status = 'pending', started_at = NULL "
                "WHERE status = 'running' AND started_at < ?",
                (iso(cutoff),),
            )
            return cursor.rowcount

    def list_jobs(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [
            {
                "id": row["id"],
                "kind": row["kind"],
                "status": row["status"],
                "due_at": row["due_at"],
                "created_at": row["created_at"],
                "started_at": row["started_at"],
                "finished_at": row["finished_at"],
                "error": row["error"],
                "payload": json_loads(row["payload_json"], {}),
            }
            for row in rows
        ]


class SchedulerThread:
    def __init__(self, scheduler: Scheduler, interval_seconds: int = 60) -> None:
        self.scheduler = scheduler
        self.interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="knowgraph-scheduler", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.scheduler.tick()
            except Exception:
                pass
            self._stop.wait(self.interval_seconds)
