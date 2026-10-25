"""Application settings with environment defaults and database overrides."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from .db import Database


@dataclass(frozen=True)
class Paths:
    home: Path
    vault: Path
    db_path: Path
    resume_dir: Path


def _load_dotenv() -> None:
    """Load a simple KEY=VALUE .env from the working directory, without overriding env."""
    path = Path.cwd() / ".env"
    if not path.exists():
        return
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv()


def load_paths() -> Paths:
    base = Path.cwd()
    home = Path(os.getenv("KNOWGRAPH_HOME", base / "data")).expanduser().resolve()
    vault = Path(os.getenv("KNOWGRAPH_VAULT", base / "vault")).expanduser().resolve()
    resume_dir = home / "resume"
    return Paths(home=home, vault=vault, db_path=home / "knowgraph.sqlite3", resume_dir=resume_dir)


DEFAULTS = {
    "timezone": os.getenv("KNOWGRAPH_TIMEZONE", "Asia/Shanghai"),
    "model": os.getenv("KNOWGRAPH_MODEL", "claude-opus-5-5"),
    "llm_enabled": "1" if os.getenv("KNOWGRAPH_LLM_ENABLED", "1") != "0" else "0",
    "collection_limit": os.getenv("KNOWGRAPH_COLLECTION_LIMIT", "12"),
    "track_interactions": "1",
    "qa_history_enabled": "1",
    "interest_half_life_days": "14",
    "interest_expiry_days": "45",
    "weekly_review_day": "1",
    "weekly_review_hour": "8",
    "monthly_review_day": "1",
    "monthly_review_hour": "8",
    "allow_private_urls": "0",
}


class RuntimeSettings:
    def __init__(self, db: Database) -> None:
        self.db = db
        self._seed_defaults()

    def _seed_defaults(self) -> None:
        with self.db.connect() as conn:
            for key, value in DEFAULTS.items():
                conn.execute(
                    "INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO NOTHING",
                    (key, value),
                )

    def get(self, key: str, default: str | None = None) -> str:
        with self.db.connect() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else (DEFAULTS.get(key, default) if default is None else default)

    def get_bool(self, key: str) -> bool:
        return self.get(key, "0").strip() in {"1", "true", "yes", "on"}

    def get_int(self, key: str, default: int = 0) -> int:
        try:
            return int(self.get(key, str(default)))
        except ValueError:
            return default

    def set(self, key: str, value: str) -> None:
        if key not in DEFAULTS:
            raise KeyError(f"Unknown setting: {key}")
        with self.db.connect() as conn:
            conn.execute(
                "INSERT INTO settings(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, str(value)),
            )

    def public(self) -> dict[str, str]:
        with self.db.connect() as conn:
            rows = conn.execute("SELECT key, value FROM settings ORDER BY key").fetchall()
        result = dict(DEFAULTS)
        result.update({row["key"]: row["value"] for row in rows})
        result.pop("llm_api_key", None)
        return result

    def timezone(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.get("timezone"))
        except Exception:
            return ZoneInfo("UTC")
