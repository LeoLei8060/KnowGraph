"""SQLite persistence. Structured state lives here; note prose lives in the Vault."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS profile (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    name TEXT NOT NULL DEFAULT '',
    headline TEXT NOT NULL DEFAULT '',
    location TEXT NOT NULL DEFAULT '',
    resume_text TEXT NOT NULL DEFAULT '',
    resume_filename TEXT,
    links_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS profile_revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    field TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    source TEXT NOT NULL DEFAULT 'user',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS profile_suggestions (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT '',
    level INTEGER,
    evidence TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS skills (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT '',
    slug TEXT NOT NULL UNIQUE,
    level INTEGER NOT NULL DEFAULT 2,
    years REAL,
    notes TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'user',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS goals (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('short', 'long')),
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    due_date TEXT,
    priority INTEGER NOT NULL DEFAULT 2,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    platform TEXT NOT NULL DEFAULT '',
    config_json TEXT NOT NULL DEFAULT '{}',
    enabled INTEGER NOT NULL DEFAULT 1,
    interval_minutes INTEGER NOT NULL DEFAULT 1440,
    last_run_at TEXT,
    last_status TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS items (
    id TEXT PRIMARY KEY,
    source_key TEXT NOT NULL UNIQUE,
    source_id TEXT,
    source_type TEXT NOT NULL,
    canonical_url TEXT,
    title TEXT NOT NULL,
    title_zh TEXT NOT NULL DEFAULT '',
    author TEXT,
    published_at TEXT,
    fetched_at TEXT NOT NULL,
    content_scope TEXT NOT NULL DEFAULT 'metadata',
    summary TEXT NOT NULL DEFAULT '',
    excerpt TEXT NOT NULL DEFAULT '',
    content_text TEXT NOT NULL DEFAULT '',
    language TEXT NOT NULL DEFAULT '',
    organization TEXT NOT NULL DEFAULT 'fallback',
    organized_at TEXT,
    content_hash TEXT NOT NULL,
    tags_json TEXT NOT NULL DEFAULT '[]',
    matched_json TEXT NOT NULL DEFAULT '[]',
    why TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'inbox',
    vault_path TEXT,
    raw_meta_json TEXT NOT NULL DEFAULT '{}',
    first_seen_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS items_status_idx ON items(status);
CREATE INDEX IF NOT EXISTS items_source_idx ON items(source_id);

CREATE TABLE IF NOT EXISTS item_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    content_hash TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    vault_path TEXT,
    note TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS tags (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    aliases_json TEXT NOT NULL DEFAULT '[]',
    parent_slug TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS note_index (
    note_id TEXT PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    tags_json TEXT NOT NULL DEFAULT '[]',
    content_hash TEXT NOT NULL,
    managed_hash TEXT,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS note_index_kind_idx ON note_index(kind);

CREATE TABLE IF NOT EXISTS note_tags (
    note_id TEXT NOT NULL REFERENCES note_index(note_id) ON DELETE CASCADE,
    tag TEXT NOT NULL,
    PRIMARY KEY (note_id, tag)
);
CREATE INDEX IF NOT EXISTS note_tags_tag_idx ON note_tags(tag);

CREATE VIRTUAL TABLE IF NOT EXISTS note_fts USING fts5(
    note_id UNINDEXED,
    title,
    body,
    tags,
    tokenize = 'unicode61 remove_diacritics 2'
);

CREATE TABLE IF NOT EXISTS interactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    query TEXT NOT NULL DEFAULT '',
    item_id TEXT,
    note_id TEXT,
    topics_json TEXT NOT NULL DEFAULT '[]',
    meta_json TEXT NOT NULL DEFAULT '{}',
    excluded INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS interactions_created_idx ON interactions(created_at);

CREATE TABLE IF NOT EXISTS interests (
    slug TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    score REAL NOT NULL DEFAULT 0,
    signals INTEGER NOT NULL DEFAULT 0,
    last_signal_at TEXT,
    pinned INTEGER NOT NULL DEFAULT 0,
    blocked INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT 'auto',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    dedupe_key TEXT NOT NULL UNIQUE,
    due_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    payload_json TEXT NOT NULL DEFAULT '{}',
    started_at TEXT,
    finished_at TEXT,
    error TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_status_idx ON jobs(status, due_at);

CREATE TABLE IF NOT EXISTS reviews (
    id TEXT PRIMARY KEY,
    period_type TEXT NOT NULL,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    revision INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    summary_md TEXT NOT NULL DEFAULT '',
    stats_json TEXT NOT NULL DEFAULT '{}',
    vault_path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (period_type, period_start, period_end, revision)
);

CREATE TABLE IF NOT EXISTS review_evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    review_id TEXT NOT NULL REFERENCES reviews(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    ref_id TEXT,
    title TEXT,
    detail TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS progress_logs (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    skill TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'done',
    minutes INTEGER NOT NULL DEFAULT 0,
    notes TEXT NOT NULL DEFAULT '',
    occurred_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS qa_groups (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS qa_history (
    id TEXT PRIMARY KEY,
    group_id TEXT REFERENCES qa_groups(id) ON DELETE SET NULL,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    mode TEXT NOT NULL,
    grounded INTEGER NOT NULL DEFAULT 0,
    citations_json TEXT NOT NULL DEFAULT '[]',
    evidence_json TEXT NOT NULL DEFAULT '[]',
    notice TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS qa_history_created_idx ON qa_history(created_at);
CREATE INDEX IF NOT EXISTS qa_history_group_idx ON qa_history(group_id);

CREATE TABLE IF NOT EXISTS llm_profiles (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    preset_key TEXT,
    family TEXT NOT NULL DEFAULT '',
    protocol TEXT NOT NULL,
    auth_style TEXT NOT NULL DEFAULT 'bearer',
    base_url TEXT NOT NULL,
    model TEXT NOT NULL DEFAULT '',
    models_json TEXT NOT NULL DEFAULT '[]',
    website_url TEXT NOT NULL DEFAULT '',
    docs_url TEXT NOT NULL DEFAULT '',
    api_key_url TEXT NOT NULL DEFAULT '',
    extra_headers_json TEXT NOT NULL DEFAULT '{}',
    requires_key INTEGER NOT NULL DEFAULT 1,
    active INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    ref_id TEXT,
    detail_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
"""


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def json_loads(value: str | None, default: Any = None) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


class Database:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> "contextmanager[Any]":
        return _connect(self.path)

    def init(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            self._ensure_columns(conn)
            conn.execute("PRAGMA user_version = 1")

    def _ensure_columns(self, conn: sqlite3.Connection) -> None:
        """Small additive migrations for existing local databases."""
        additions = {
            "skills": {"category": "TEXT NOT NULL DEFAULT ''"},
            "profile_suggestions": {"category": "TEXT NOT NULL DEFAULT ''"},
            "items": {
                "title_zh": "TEXT NOT NULL DEFAULT ''",
                "content_text": "TEXT NOT NULL DEFAULT ''",
                "language": "TEXT NOT NULL DEFAULT ''",
                "organization": "TEXT NOT NULL DEFAULT 'fallback'",
                "organized_at": "TEXT",
            },
            "sources": {"platform": "TEXT NOT NULL DEFAULT ''"},
        }
        for table, columns in additions.items():
            existing = {
                row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
            }
            for column, definition in columns.items():
                if column not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def backup(self, destination: Path) -> Path:
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn, sqlite3.connect(destination) as target:
            conn.backup(target)
        return destination


@contextmanager
def _connect(path: Path) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(path, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 8000")
    conn.execute("PRAGMA journal_mode = WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
