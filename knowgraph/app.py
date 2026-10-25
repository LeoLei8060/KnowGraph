"""Application composition root."""

from __future__ import annotations

from typing import Any

from .config import Paths, RuntimeSettings, load_paths
from .credentials import CredentialStore
from .db import Database
from .github_rankings import GitHubRankingService
from .history import QAHistoryService
from .ingest import IngestService, SourceService
from .interests import InterestService
from .llm import LLMService
from .profile import ProfileService
from .progress import ProgressService
from .providers import ProviderService
from .qa import QAService
from .reviews import ReviewService
from .scheduler import Scheduler, SchedulerThread
from .search import SearchService
from .tags import TagService
from .vault import Vault
from .utils import iso


class Application:
    def __init__(self, paths: Paths | None = None) -> None:
        self.paths = paths or load_paths()
        self.paths.home.mkdir(parents=True, exist_ok=True)
        self.paths.resume_dir.mkdir(parents=True, exist_ok=True)
        self.db = Database(self.paths.db_path)
        self.db.init()
        self.settings = RuntimeSettings(self.db)
        self.vault = Vault(self.paths.vault, self.db)
        self.tags = TagService(self.db)
        self.credentials = CredentialStore(self.paths.home)
        self.providers = ProviderService(self.db, self.settings, self.credentials)
        self.llm = LLMService(self.db, self.settings, self.providers)
        self.profile = ProfileService(self.db, self.llm)
        self.interests = InterestService(self.db, self.settings, self.tags)
        self.search = SearchService(self.db, self.tags)
        self.progress = ProgressService(self.db)
        self.history = QAHistoryService(self.db, self.vault, self.settings)
        self.github_rankings = GitHubRankingService(self.llm)
        self.qa = QAService(self.db, self.vault, self.search, self.llm, self.interests, self.history)
        self.ingest = IngestService(
            self.db, self.settings, self.vault, self.tags, self.profile, self.interests, self.llm
        )
        self.sources = SourceService(self.db, self.profile, self.interests)
        self.reviews = ReviewService(self.db, self.settings, self.vault, self.llm, self.interests)
        self.scheduler = Scheduler(self.db, self.settings, self.ingest, self.reviews)
        self._thread: SchedulerThread | None = None

    def start_scheduler(self) -> None:
        if self._thread is None:
            self._thread = SchedulerThread(self.scheduler)
            self._thread.start()

    def stop_scheduler(self) -> None:
        if self._thread is not None:
            self._thread.stop()
            self._thread = None

    def status(self) -> dict[str, Any]:
        with self.db.connect() as conn:
            counts = {
                "items": conn.execute("SELECT COUNT(*) AS n FROM items").fetchone()["n"],
                "inbox": conn.execute(
                    "SELECT COUNT(*) AS n FROM items WHERE status IN ('inbox','read')"
                ).fetchone()["n"],
                "notes": conn.execute("SELECT COUNT(*) AS n FROM note_index").fetchone()["n"],
                "sources": conn.execute("SELECT COUNT(*) AS n FROM sources").fetchone()["n"],
                "reviews": conn.execute("SELECT COUNT(*) AS n FROM reviews").fetchone()["n"],
            }
            last_job = conn.execute(
                "SELECT kind, status, finished_at, error FROM jobs ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        return {
            "app": "knowgraph",
            "version": "0.1.0",
            "home": str(self.paths.home),
            "vault": str(self.paths.vault),
            "timezone": self.settings.get("timezone"),
            "llm": self.llm.status(),
            "counts": counts,
            "scheduler_running": self._thread is not None,
            "last_job": dict(last_job) if last_job else None,
            "time": iso(),
        }
