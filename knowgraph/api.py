"""Local HTTP API. The UI, CLI and future workbench share these contracts."""

from __future__ import annotations

import mimetypes
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from .app import Application
from .config import DEFAULTS
from .github_rankings import GitHubRankingError
from .llm import LLMUnavailable
from .utils import filename_slug

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/css", ".css")


class ResumeIn(BaseModel):
    text: str = Field(min_length=1, max_length=200_000)
    filename: str | None = None


class SkillIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    category: str = ""
    level: int = 2
    years: float | None = None
    notes: str = ""


class GoalIn(BaseModel):
    kind: str
    title: str = Field(min_length=1, max_length=160)
    description: str = ""
    status: str = "active"
    due_date: str | None = None
    priority: int = 2


class SourceIn(BaseModel):
    kind: str
    name: str = Field(min_length=1, max_length=120)
    platform: str = ""
    config: dict[str, Any] = Field(default_factory=dict)
    interval_minutes: int = 1440
    enabled: bool = True


class FeedbackIn(BaseModel):
    status: str


class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    mode: str = "auto"
    top_k: int = 8
    group_id: str | None = None


class LLMKeyIn(BaseModel):
    api_key: str = Field(min_length=8, max_length=400)


class QAGroupIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class QAHistoryPatch(BaseModel):
    group_id: str | None = None


class ProviderIn(BaseModel):
    preset_key: str | None = None
    name: str | None = None
    protocol: str | None = None
    auth_style: str | None = None
    base_url: str | None = None
    model: str | None = None
    models: list[str] | None = None
    website_url: str | None = None
    docs_url: str | None = None
    api_key_url: str | None = None
    extra_headers: dict[str, str] | None = None
    requires_key: bool | None = None
    api_key: str | None = Field(default=None, min_length=8, max_length=400)
    activate: bool = False


class ReviewIn(BaseModel):
    period_type: str = "week"
    offset: int = -1
    force: bool = False


class ProgressIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    skill: str = ""
    status: str = "done"
    minutes: int = 0
    notes: str = ""
    occurred_at: str | None = None


def create_app(application: Application | None = None) -> FastAPI:
    services = application or Application()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        services.start_scheduler()
        try:
            yield
        finally:
            services.stop_scheduler()

    app = FastAPI(title="KnowGraph", version="0.1.0", docs_url="/docs", redoc_url=None, lifespan=lifespan)
    app.state.services = services
    app.state.owns_services = application is None

    @app.middleware("http")
    async def safety_headers(request: Request, call_next):
        if request.method in {"POST", "PATCH", "PUT", "DELETE"}:
            origin = request.headers.get("origin")
            if origin:
                origin_host = urlsplit(origin).netloc
                request_host = request.headers.get("host", "")
                if origin_host and origin_host != request_host:
                    return JSONResponse({"detail": "Cross-origin write blocked"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return services.status()

    # Profile and stable facts -------------------------------------------------
    @app.get("/api/profile")
    def get_profile() -> dict[str, Any]:
        return services.profile.get()

    @app.patch("/api/profile")
    def patch_profile(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        allowed = {"name", "headline", "location", "resume_text", "resume_filename", "links"}
        patch = {key: value for key, value in payload.items() if key in allowed}
        return services.profile.update(patch)

    @app.post("/api/profile/resume")
    def save_resume(payload: ResumeIn) -> dict[str, Any]:
        filename = payload.filename or "resume.md"
        target = services.paths.resume_dir / filename_slug(filename)
        target.write_text(payload.text, encoding="utf-8")
        result = services.profile.save_resume(payload.text, filename=filename)
        result["saved_to"] = str(target)
        return result

    @app.post("/api/profile/resume-file")
    def upload_resume(file: UploadFile = File(...)) -> dict[str, Any]:
        raw = file.file.read(10_000_001)
        if len(raw) > 10_000_000:
            raise HTTPException(413, "Resume file is larger than 10 MB")
        filename = Path(file.filename or "resume.txt").name
        suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename else "txt"
        if suffix == "pdf":
            try:
                from io import BytesIO

                from pypdf import PdfReader

                reader = PdfReader(BytesIO(raw))
                text = "\n\n".join((page.extract_text() or "") for page in reader.pages).strip()
            except Exception as exc:
                raise HTTPException(400, f"Could not read PDF: {exc}")
            if not text:
                raise HTTPException(400, "PDF contains no extractable text")
        elif suffix in {"txt", "md", "markdown"}:
            try:
                text = raw.decode("utf-8-sig")
            except UnicodeDecodeError:
                try:
                    text = raw.decode("gb18030")
                except UnicodeDecodeError:
                    raise HTTPException(400, "Resume text must be UTF-8 or GB18030")
        else:
            raise HTTPException(400, "Upload a .pdf, .txt, or .md resume")
        target = services.paths.resume_dir / filename_slug(filename)
        target.write_bytes(raw)
        result = services.profile.save_resume(text, filename=filename)
        result["saved_to"] = str(target)
        return result

    @app.post("/api/profile/suggestions/{suggestion_id}/accept")
    def accept_suggestion(suggestion_id: str) -> dict[str, Any]:
        try:
            return services.profile.accept_suggestion(suggestion_id)
        except KeyError:
            raise HTTPException(404, "Suggestion not found")

    @app.delete("/api/profile/suggestions/{suggestion_id}")
    def dismiss_suggestion(suggestion_id: str) -> dict[str, Any]:
        services.profile.dismiss_suggestion(suggestion_id)
        return {"ok": True}

    @app.post("/api/profile/suggestions/accept-all")
    def accept_all_suggestions() -> dict[str, Any]:
        return services.profile.accept_all_suggestions()

    @app.delete("/api/profile/suggestions")
    def clear_suggestions() -> dict[str, Any]:
        services.profile.clear_suggestions()
        return {"ok": True}

    @app.post("/api/skills")
    def add_skill(payload: SkillIn) -> dict[str, Any]:
        return services.profile.add_skill(
            payload.name,
            payload.level,
            payload.years,
            payload.notes,
            source="user",
            category=payload.category,
        )

    @app.patch("/api/skills/{skill_id}")
    def patch_skill(skill_id: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        try:
            return services.profile.update_skill(skill_id, payload)
        except KeyError:
            raise HTTPException(404, "Skill not found")

    @app.delete("/api/skills/{skill_id}")
    def delete_skill(skill_id: str) -> dict[str, Any]:
        services.profile.delete_skill(skill_id)
        return {"ok": True}

    @app.post("/api/goals")
    def add_goal(payload: GoalIn) -> dict[str, Any]:
        try:
            return services.profile.add_goal(
                payload.kind,
                payload.title,
                payload.description,
                payload.status,
                payload.due_date,
                payload.priority,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    @app.patch("/api/goals/{goal_id}")
    def patch_goal(goal_id: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        try:
            return services.profile.update_goal(goal_id, payload)
        except KeyError:
            raise HTTPException(404, "Goal not found")
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    @app.delete("/api/goals/{goal_id}")
    def delete_goal(goal_id: str) -> dict[str, Any]:
        services.profile.delete_goal(goal_id)
        return {"ok": True}

    # Sources and collection --------------------------------------------------
    @app.get("/api/sources")
    def list_sources() -> dict[str, Any]:
        return {"sources": services.sources.list()}

    @app.post("/api/sources")
    def add_source(payload: SourceIn) -> dict[str, Any]:
        try:
            _validate_source(payload.kind, payload.config)
            return services.sources.add(
                payload.kind,
                payload.name,
                payload.config,
                payload.interval_minutes,
                payload.enabled,
                platform=payload.platform,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    @app.patch("/api/sources/{source_id}")
    def patch_source(source_id: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        try:
            if "config" in payload and "kind" not in payload:
                current = services.sources.get(source_id)
                if current is None:
                    raise KeyError(source_id)
                _validate_source(current["kind"], payload["config"])
            return services.sources.update(source_id, payload)
        except KeyError:
            raise HTTPException(404, "Source not found")
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    @app.delete("/api/sources/{source_id}")
    def delete_source(source_id: str) -> dict[str, Any]:
        services.sources.delete(source_id)
        return {"ok": True}

    @app.get("/api/collection/plan")
    def collection_plan() -> dict[str, Any]:
        return services.sources.plan()

    @app.post("/api/collect")
    def collect_now() -> dict[str, Any]:
        return services.scheduler.run_now("collect_all", {})

    # Inbox and items ---------------------------------------------------------
    @app.get("/api/inbox")
    def inbox(status: str | None = None, limit: int = Query(60, ge=1, le=200)) -> dict[str, Any]:
        return {"items": services.ingest.list_items(status=status, limit=limit)}

    @app.get("/api/news")
    def news(days: int = Query(7, ge=1, le=30), limit: int = Query(100, ge=1, le=300)) -> dict[str, Any]:
        return {"items": services.ingest.list_news(days=days, limit=limit), "days": days}

    @app.get("/api/github/rankings")
    def github_rankings(
        mode: str = "trending_weekly",
        category: str = "all",
        limit: int = Query(12, ge=1, le=30),
    ) -> dict[str, Any]:
        try:
            return services.github_rankings.fetch(mode, category, limit)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        except GitHubRankingError as exc:
            raise HTTPException(502, str(exc))
        except Exception as exc:
            raise HTTPException(502, f"GitHub ranking request failed: {exc}")

    @app.get("/api/items/{item_id}")
    def get_item(item_id: str) -> dict[str, Any]:
        item = services.ingest.get_item(item_id)
        if item is None:
            raise HTTPException(404, "Item not found")
        return item

    @app.post("/api/items/{item_id}/feedback")
    def item_feedback(item_id: str, payload: FeedbackIn) -> dict[str, Any]:
        try:
            return services.ingest.feedback(item_id, payload.status)
        except KeyError:
            raise HTTPException(404, "Item not found")
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    # Search, notes and Q&A ---------------------------------------------------
    @app.get("/api/search")
    def search(q: str = "", tags: str = "", limit: int = Query(20, ge=1, le=100)) -> dict[str, Any]:
        result = services.search.search(q, tags=tags, limit=limit)
        if q.strip() or tags.strip():
            services.interests.record_interaction(
                "search", " ".join(part for part in [q.strip(), tags.strip()] if part)
            )
        return result

    @app.get("/api/notes/{note_id}")
    def get_note(note_id: str) -> dict[str, Any]:
        note = services.vault.read_note(note_id)
        if note is None:
            raise HTTPException(404, "Note not found")
        return note

    @app.post("/api/ask")
    def ask(payload: AskIn) -> dict[str, Any]:
        if payload.mode not in {"auto", "local"}:
            raise HTTPException(400, "mode must be auto or local")
        return services.qa.ask(
            payload.question,
            mode=payload.mode,
            top_k=payload.top_k,
            group_id=payload.group_id,
        )

    # Q&A history and groups -------------------------------------------------
    @app.get("/api/qa/groups")
    def qa_groups() -> dict[str, Any]:
        return services.history.groups()

    @app.post("/api/qa/groups")
    def create_qa_group(payload: QAGroupIn) -> dict[str, Any]:
        try:
            return services.history.create_group(payload.name)
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    @app.patch("/api/qa/groups/{group_id}")
    def rename_qa_group(group_id: str, payload: QAGroupIn) -> dict[str, Any]:
        try:
            return services.history.rename_group(group_id, payload.name)
        except KeyError:
            raise HTTPException(404, "Group not found")
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    @app.delete("/api/qa/groups/{group_id}")
    def delete_qa_group(group_id: str) -> dict[str, Any]:
        services.history.delete_group(group_id)
        return {"ok": True}

    @app.get("/api/qa/history")
    def qa_history(
        group_id: str | None = None,
        ungrouped: bool = False,
        limit: int = Query(100, ge=1, le=300),
    ) -> dict[str, Any]:
        if group_id in {None, "", "all"}:
            group_id = None
        return {"entries": services.history.list(group_id=group_id, ungrouped=ungrouped, limit=limit)}

    @app.post("/api/qa/history/clear")
    def clear_qa_history() -> dict[str, Any]:
        services.history.clear()
        return {"ok": True}

    @app.get("/api/qa/history/{entry_id}")
    def get_qa_history(entry_id: str) -> dict[str, Any]:
        try:
            return services.history.get(entry_id)
        except KeyError:
            raise HTTPException(404, "History entry not found")

    @app.patch("/api/qa/history/{entry_id}")
    def assign_qa_history(entry_id: str, payload: QAHistoryPatch) -> dict[str, Any]:
        try:
            return services.history.assign(entry_id, payload.group_id or None)
        except KeyError:
            raise HTTPException(404, "History entry or group not found")

    @app.delete("/api/qa/history/{entry_id}")
    def delete_qa_history(entry_id: str) -> dict[str, Any]:
        services.history.delete(entry_id)
        return {"ok": True}

    # Interests ---------------------------------------------------------------
    @app.get("/api/interests")
    def interests() -> dict[str, Any]:
        items = services.interests.list()
        for item in items:
            item["evidence"] = services.interests.evidence_for(item["slug"], limit=3)
        return {"interests": items, "half_life_days": services.settings.get_int("interest_half_life_days", 14)}

    @app.patch("/api/interests/{slug}")
    def patch_interest(slug: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        if "pinned" in payload:
            services.interests.set_pinned(slug, bool(payload["pinned"]))
        if "blocked" in payload:
            services.interests.set_blocked(slug, bool(payload["blocked"]))
        return {"ok": True, "interests": services.interests.list()}

    @app.delete("/api/interests/{slug}")
    def delete_interest(slug: str) -> dict[str, Any]:
        services.interests.delete_interest(slug)
        return {"ok": True}

    @app.post("/api/interests/clear")
    def clear_interests() -> dict[str, Any]:
        services.interests.clear_history()
        return {"ok": True}

    # Reviews and progress ----------------------------------------------------
    @app.get("/api/reviews")
    def list_reviews() -> dict[str, Any]:
        return {"reviews": services.reviews.list()}

    @app.get("/api/reviews/preview")
    def preview_review(period_type: str = "week", offset: int = -1) -> dict[str, Any]:
        if period_type not in {"week", "month"}:
            raise HTTPException(400, "period_type must be week or month")
        return services.reviews.preview(period_type, offset)

    @app.get("/api/reviews/{review_id}")
    def get_review(review_id: str) -> dict[str, Any]:
        try:
            return services.reviews.get(review_id)
        except KeyError:
            raise HTTPException(404, "Review not found")

    @app.post("/api/reviews/generate")
    def generate_review(payload: ReviewIn) -> dict[str, Any]:
        if payload.period_type not in {"week", "month"}:
            raise HTTPException(400, "period_type must be week or month")
        return services.reviews.generate(
            payload.period_type, offset=payload.offset, force=payload.force
        )

    @app.get("/api/progress")
    def list_progress() -> dict[str, Any]:
        return {"progress": services.progress.list()}

    @app.post("/api/progress")
    def add_progress(payload: ProgressIn) -> dict[str, Any]:
        try:
            return services.progress.add(
                payload.title,
                skill=payload.skill,
                status=payload.status,
                minutes=payload.minutes,
                notes=payload.notes,
                occurred_at=payload.occurred_at,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    @app.delete("/api/progress/{progress_id}")
    def delete_progress(progress_id: str) -> dict[str, Any]:
        services.progress.delete(progress_id)
        return {"ok": True}

    # Tags, settings, jobs and maintenance -----------------------------------
    @app.get("/api/tags")
    def list_tags() -> dict[str, Any]:
        return {"tags": services.tags.list()}

    @app.post("/api/tags")
    def add_tag(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        name = str(payload.get("name") or "").strip()
        if not name:
            raise HTTPException(400, "Tag name is empty")
        return services.tags.register(name, payload.get("aliases") or [])

    @app.post("/api/tags/{slug}/aliases")
    def add_alias(slug: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        try:
            return services.tags.add_alias(slug, str(payload.get("alias") or ""))
        except KeyError:
            raise HTTPException(404, "Tag not found")
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    @app.get("/api/settings")
    def get_settings() -> dict[str, Any]:
        return {"settings": services.settings.public(), "llm": services.llm.status()}

    # Model providers --------------------------------------------------------
    @app.get("/api/llm/presets")
    def llm_presets() -> dict[str, Any]:
        return {"presets": services.providers.presets()}

    @app.get("/api/llm/providers")
    def llm_providers() -> dict[str, Any]:
        active = services.providers.active()
        return {
            "providers": services.providers.list(),
            "active_id": active["id"] if active else None,
            "llm": services.llm.status(),
        }

    @app.get("/api/llm/providers/{provider_id}")
    def get_llm_provider(provider_id: str) -> dict[str, Any]:
        provider = services.providers.get(provider_id)
        if provider is None:
            raise HTTPException(404, "Provider not found")
        return provider

    @app.post("/api/llm/providers")
    def create_llm_provider(payload: ProviderIn) -> dict[str, Any]:
        try:
            return services.providers.create(payload.model_dump(exclude_unset=True))
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    @app.patch("/api/llm/providers/{provider_id}")
    def update_llm_provider(provider_id: str, payload: ProviderIn) -> dict[str, Any]:
        try:
            return services.providers.update(provider_id, payload.model_dump(exclude_unset=True))
        except KeyError:
            raise HTTPException(404, "Provider not found")
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    @app.delete("/api/llm/providers/{provider_id}")
    def delete_llm_provider(provider_id: str) -> dict[str, Any]:
        try:
            services.providers.delete(provider_id)
            return {"ok": True}
        except KeyError:
            raise HTTPException(404, "Provider not found")
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    @app.post("/api/llm/providers/{provider_id}/activate")
    def activate_llm_provider(provider_id: str) -> dict[str, Any]:
        try:
            return services.providers.activate(provider_id)
        except KeyError:
            raise HTTPException(404, "Provider not found")

    @app.post("/api/llm/providers/{provider_id}/key")
    def set_llm_provider_key(provider_id: str, payload: LLMKeyIn) -> dict[str, Any]:
        try:
            return services.providers.set_key(provider_id, payload.api_key)
        except KeyError:
            raise HTTPException(404, "Provider not found")
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    @app.delete("/api/llm/providers/{provider_id}/key")
    def clear_llm_provider_key(provider_id: str) -> dict[str, Any]:
        try:
            return services.providers.clear_key(provider_id)
        except KeyError:
            raise HTTPException(404, "Provider not found")

    @app.post("/api/llm/providers/{provider_id}/test")
    def test_llm_provider(provider_id: str) -> dict[str, Any]:
        try:
            return services.llm.check_connection(provider_id)
        except LLMUnavailable as exc:
            raise HTTPException(400, str(exc))

    @app.post("/api/settings/llm-key")
    def save_llm_key(payload: LLMKeyIn) -> dict[str, Any]:
        try:
            return services.llm.set_api_key(payload.api_key)
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    @app.delete("/api/settings/llm-key")
    def clear_llm_key() -> dict[str, Any]:
        return services.llm.clear_api_key()

    @app.post("/api/settings/llm-test")
    def test_llm_key() -> dict[str, Any]:
        try:
            return services.llm.check_connection()
        except LLMUnavailable as exc:
            raise HTTPException(400, str(exc))

    @app.patch("/api/settings")
    def patch_settings(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        updates: dict[str, str] = {}
        for key, value in payload.items():
            if key not in DEFAULTS:
                raise HTTPException(400, f"Unknown setting: {key}")
            if key == "timezone":
                try:
                    ZoneInfo(str(value))
                except Exception:
                    raise HTTPException(400, "Invalid timezone")
            try:
                if key in {"llm_enabled", "track_interactions", "qa_history_enabled", "allow_private_urls"}:
                    value = "1" if bool(value) else "0"
                if key == "collection_limit":
                    value = max(1, min(100, int(value)))
                if key in {"interest_half_life_days", "interest_expiry_days"}:
                    value = max(1, min(365, int(value)))
                if key in {"weekly_review_day", "monthly_review_day"}:
                    value = max(0, min(6, int(value)))
                if key in {"weekly_review_hour", "monthly_review_hour"}:
                    value = max(0, min(23, int(value)))
            except (TypeError, ValueError):
                raise HTTPException(400, f"Invalid value for setting: {key}")
            updates[key] = str(value)
        for key, value in updates.items():
            services.settings.set(key, value)
        return {"settings": services.settings.public(), "llm": services.llm.status()}

    @app.get("/api/jobs")
    def list_jobs() -> dict[str, Any]:
        return {"jobs": services.scheduler.list_jobs()}

    @app.post("/api/jobs/tick")
    def tick_jobs() -> dict[str, Any]:
        return services.scheduler.tick()

    @app.post("/api/vault/sync")
    def sync_vault() -> dict[str, Any]:
        return services.vault.sync_from_disk()

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse({"detail": "Internal error", "error": str(exc)[:300]}, status_code=500)

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

        @app.get("/")
        def index() -> FileResponse:
            return FileResponse(STATIC_DIR / "index.html")

    return app


def _validate_source(kind: str, config: dict[str, Any]) -> None:
    if not isinstance(config, dict):
        raise ValueError("config must be an object")
    if kind == "rss":
        _require_url(config.get("url"), "RSS URL")
    elif kind == "web":
        urls = config.get("urls") or ([config.get("url")] if config.get("url") else [])
        if not urls:
            raise ValueError("Web source needs url or urls")
        for url in urls:
            _require_url(url, "Web URL")
    elif kind == "github":
        repos = config.get("repos") or []
        if isinstance(repos, str):
            repos = [part.strip() for part in repos.split(",") if part.strip()]
        if not repos:
            raise ValueError("GitHub source needs repos")
        for repo in repos:
            if not re.fullmatch(r"[\w.-]+/[\w.-]+", str(repo)):
                raise ValueError(f"Invalid GitHub repository: {repo}")
    elif kind == "arxiv":
        if not (config.get("categories") or config.get("query")):
            raise ValueError("arXiv source needs categories or query")
    else:
        raise ValueError(f"Unsupported source kind: {kind}")


def _require_url(value: Any, label: str) -> None:
    text = str(value or "").strip()
    if not text.startswith(("http://", "https://")):
        raise ValueError(f"{label} must start with http:// or https://")
