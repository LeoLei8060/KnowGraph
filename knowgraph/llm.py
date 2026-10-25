"""Model access across Anthropic Messages and OpenAI-compatible providers."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

import httpx

from .config import RuntimeSettings
from .db import Database, json_dumps
from .providers import ProviderService
from .utils import iso

try:  # Imported lazily in tests that do not exercise live calls.
    import anthropic
except Exception:  # pragma: no cover - only when dependency is absent
    anthropic = None


class LLMUnavailable(RuntimeError):
    pass


class LLMRefusal(RuntimeError):
    pass


@dataclass
class LLMResult:
    text: str
    model: str
    usage: dict[str, Any]


class LLMService:
    def __init__(self, db: Database, settings: RuntimeSettings, providers: ProviderService) -> None:
        self.db = db
        self.settings = settings
        self.providers = providers
        self._client: Any = None
        self._client_key: str | None = None
        self._client_error: str | None = None
        self._verified_provider_id: str | None = None

    def _active(self) -> dict[str, Any] | None:
        return self.providers.active()

    def credentials_hint(self) -> bool:
        provider = self._active()
        return bool(provider and provider.get("configured"))

    @property
    def available(self) -> bool:
        if not self.settings.get_bool("llm_enabled"):
            return False
        provider = self._active()
        if provider is None or not provider.get("configured"):
            return False
        if provider.get("protocol") == "anthropic" and anthropic is None:
            return False
        return True

    def status(self) -> dict[str, Any]:
        provider = self._active() or {}
        return {
            "available": self.available,
            "configured": bool(provider.get("configured")),
            "enabled": self.settings.get_bool("llm_enabled"),
            "provider_id": provider.get("id"),
            "provider_name": provider.get("name"),
            "protocol": provider.get("protocol"),
            "base_url": provider.get("base_url"),
            "model": provider.get("model"),
            "auth_style": provider.get("auth_style"),
            "key_source": provider.get("source", "none"),
            "key_hint": provider.get("hint"),
            "verified": bool(provider and self._verified_provider_id == provider.get("id")),
            "error": self._client_error,
        }

    def set_api_key(self, api_key: str) -> dict[str, Any]:
        provider = self._active()
        if provider is None:
            raise LLMUnavailable("No model provider is configured.")
        self.providers.set_key(provider["id"], api_key)
        self._reset_client()
        return self.status()

    def clear_api_key(self) -> dict[str, Any]:
        provider = self._active()
        if provider is None:
            raise LLMUnavailable("No model provider is configured.")
        self.providers.clear_key(provider["id"])
        self._reset_client()
        return self.status()

    def check_connection(self, provider_id: str | None = None) -> dict[str, Any]:
        provider = self.providers.get(provider_id) if provider_id else self._active()
        if provider is None:
            raise LLMUnavailable("No model provider is configured.")
        key = self.providers.key_for(provider)
        if provider["requires_key"] and not key:
            raise LLMUnavailable("No API key is configured for this provider.")
        try:
            if provider["protocol"] == "openai":
                result = self._check_openai(provider, key)
            else:
                result = self._check_anthropic(provider, key)
            if provider_id is None or (self._active() or {}).get("id") == provider["id"]:
                self._verified_provider_id = provider["id"]
                self._client_error = None
            return {**result, "provider_id": provider["id"], "provider_name": provider["name"]}
        except Exception as exc:
            if provider_id is None or (self._active() or {}).get("id") == provider["id"]:
                self._verified_provider_id = None
            self._record_failure(exc)
            raise LLMUnavailable(str(exc)) from exc

    def complete(
        self,
        system: str,
        prompt: str,
        *,
        max_tokens: int = 2000,
        effort: str = "medium",
    ) -> LLMResult:
        provider = self._active()
        if provider is None or not self.available:
            raise LLMUnavailable("No usable model provider is configured; using local fallback.")
        key = self.providers.key_for(provider)
        if provider["requires_key"] and not key:
            raise LLMUnavailable("No API key is configured for this provider.")
        if provider["protocol"] == "openai":
            result = self._complete_openai(provider, key, system, prompt, max_tokens)
        else:
            result = self._complete_anthropic(provider, key, system, prompt, max_tokens, effort)
        self._record_success(provider, result.model, result.usage)
        self._verified_provider_id = provider["id"]
        self._client_error = None
        return result

    def complete_json(
        self,
        system: str,
        prompt: str,
        *,
        max_tokens: int = 1600,
        effort: str = "medium",
    ) -> dict[str, Any] | None:
        result = self.complete(system, prompt, max_tokens=max_tokens, effort=effort)
        return parse_json_object(result.text)

    # Anthropic Messages API -------------------------------------------------
    def _anthropic_client(self, provider: dict[str, Any], key: str | None) -> Any:
        if anthropic is None:
            raise LLMUnavailable("The anthropic package is not installed.")
        cache_key = provider["id"] + ":" + (key or "")
        if self._client is not None and self._client_key == cache_key:
            return self._client
        kwargs: dict[str, Any] = {
            "base_url": provider["base_url"] or None,
            "timeout": 240.0,
            "max_retries": 2,
        }
        if key:
            if provider["auth_style"] == "bearer":
                kwargs["auth_token"] = key
            else:
                kwargs["api_key"] = key
        headers = provider.get("extra_headers") or {}
        if headers:
            kwargs["default_headers"] = headers
        try:
            self._client = anthropic.Anthropic(**kwargs)
            self._client_key = cache_key
        except Exception as exc:
            self._client_error = str(exc)
            raise LLMUnavailable(str(exc)) from exc
        return self._client

    def _complete_anthropic(
        self,
        provider: dict[str, Any],
        key: str | None,
        system: str,
        prompt: str,
        max_tokens: int,
        effort: str,
    ) -> LLMResult:
        client = self._anthropic_client(provider, key)
        model = provider["model"]
        official = (
            provider.get("preset_key") == "anthropic"
            or "api.anthropic.com" in (provider.get("base_url") or "")
        )
        budget = max(1, max_tokens)
        last_error: Exception | None = None

        for _ in range(3):
            request = {
                "model": model,
                "max_tokens": budget,
                "system": system,
                "messages": [{"role": "user", "content": prompt}],
            }
            with_effort = {**request, "output_config": {"effort": effort}}
            if official:
                attempts = [
                    lambda r=with_effort: client.beta.messages.create(
                        betas=["server-side-fallback-2026-07-01"],
                        fallbacks="default",
                        **r,
                    ),
                    lambda r=with_effort: client.messages.create(**r),
                    lambda r=request: client.messages.create(**r),
                    lambda r=request: client.beta.messages.create(
                        betas=["server-side-fallback-2026-07-01"],
                        fallbacks="default",
                        **r,
                    ),
                ]
            else:
                # Reasoning-capable relays (e.g. DeepSeek) can spend the whole
                # output budget on hidden thinking. Ask them to disable it and
                # fall back for relays that reject the field.
                relay_request = {**request, "thinking": {"type": "disabled"}}
                attempts = [
                    lambda r=relay_request: client.messages.create(**r),
                    lambda r=request: client.messages.create(**r),
                ]

            response = None
            for attempt in attempts:
                try:
                    response = attempt()
                    break
                except Exception as exc:
                    last_error = exc
                    if anthropic and isinstance(exc, anthropic.APIStatusError) and exc.status_code in {400, 422}:
                        continue
                    if isinstance(exc, TypeError):
                        continue
                    self._record_failure(exc)
                    raise LLMUnavailable(str(exc)) from exc
            if response is None:
                error = last_error or LLMUnavailable("Model request failed.")
                self._record_failure(error)
                raise LLMUnavailable(str(error))
            if getattr(response, "stop_reason", None) == "refusal":
                raise LLMRefusal("The model declined this request.")
            text = "\n".join(
                block.text for block in response.content if getattr(block, "type", "") == "text"
            ).strip()
            if text:
                usage = {
                    "input_tokens": getattr(response.usage, "input_tokens", None),
                    "output_tokens": getattr(response.usage, "output_tokens", None),
                }
                return LLMResult(text=text, model=model, usage=usage)
            # Reasoning-capable models may spend the whole output budget on
            # hidden thinking. Retry once or twice with more room.
            if getattr(response, "stop_reason", None) == "max_tokens" and budget < 16000:
                budget = min(16000, max(4000, budget * 3))
                continue
            break
        raise LLMUnavailable("The model returned an empty response.")

    def _check_anthropic(self, provider: dict[str, Any], key: str | None) -> dict[str, Any]:
        client = self._anthropic_client(provider, key)
        try:
            response = client.messages.count_tokens(
                model=provider["model"],
                messages=[{"role": "user", "content": "ping"}],
            )
            return {"ok": True, "model": provider["model"], "input_tokens": response.input_tokens}
        except Exception as exc:
            if anthropic and isinstance(exc, (anthropic.BadRequestError, anthropic.NotFoundError)):
                response = client.messages.create(
                    model=provider["model"],
                    max_tokens=1,
                    messages=[{"role": "user", "content": "ping"}],
                )
                return {
                    "ok": True,
                    "model": provider["model"],
                    "input_tokens": getattr(response.usage, "input_tokens", None),
                }
            raise

    # OpenAI Chat Completions API -------------------------------------------
    def _openai_url(self, provider: dict[str, Any], suffix: str) -> str:
        base = provider["base_url"].rstrip("/")
        if base.endswith("/chat/completions"):
            base = base[: -len("/chat/completions")]
        if suffix == "chat/completions":
            return base + "/chat/completions"
        return base + "/" + suffix.lstrip("/")

    def _openai_headers(self, provider: dict[str, Any], key: str | None) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        for name, value in (provider.get("extra_headers") or {}).items():
            headers[str(name)] = str(value)
        return headers

    def _complete_openai(
        self,
        provider: dict[str, Any],
        key: str | None,
        system: str,
        prompt: str,
        max_tokens: int,
    ) -> LLMResult:
        url = self._openai_url(provider, "chat/completions")
        headers = self._openai_headers(provider, key)
        payload: dict[str, Any] = {
            "model": provider["model"],
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": max_tokens,
        }
        with httpx.Client(timeout=120.0, follow_redirects=True) as client:
            response = client.post(url, headers=headers, json=payload)
            if response.status_code == 400 and "max_tokens" in response.text.lower():
                payload.pop("max_tokens", None)
                payload["max_completion_tokens"] = max_tokens
                response = client.post(url, headers=headers, json=payload)
        if response.status_code >= 400:
            detail = response.text[:500]
            self._record_failure(RuntimeError(f"HTTP {response.status_code}: {detail}"))
            raise LLMUnavailable(f"HTTP {response.status_code}: {detail}")
        try:
            data = response.json()
        except ValueError as exc:
            raise LLMUnavailable("The provider returned non-JSON data.") from exc
        choices = data.get("choices") or []
        if not choices:
            raise LLMUnavailable("The provider returned no choices.")
        content = choices[0].get("message", {}).get("content", "")
        if isinstance(content, list):
            content = "\n".join(
                part.get("text", "") for part in content if isinstance(part, dict)
            )
        text = str(content or "").strip()
        if not text:
            raise LLMUnavailable("The provider returned an empty response.")
        usage = data.get("usage") or {}
        return LLMResult(
            text=text,
            model=provider["model"],
            usage={
                "input_tokens": usage.get("prompt_tokens"),
                "output_tokens": usage.get("completion_tokens"),
            },
        )

    def _check_openai(self, provider: dict[str, Any], key: str | None) -> dict[str, Any]:
        headers = self._openai_headers(provider, key)
        url = self._openai_url(provider, "models")
        try:
            with httpx.Client(timeout=25.0, follow_redirects=True) as client:
                response = client.get(url, headers=headers)
            if response.status_code < 400:
                data = response.json()
                models = data.get("data") or data.get("models") or []
                return {
                    "ok": True,
                    "model": provider["model"],
                    "models": len(models) if isinstance(models, list) else None,
                }
        except Exception:
            pass
        # Some compatible relays do not implement /models. Use one minimal chat.
        result = self._complete_openai(provider, key, "Reply with OK.", "ping", 1)
        return {"ok": True, "model": result.model, "input_tokens": result.usage.get("input_tokens")}

    def _reset_client(self) -> None:
        self._client = None
        self._client_key = None
        self._client_error = None
        self._verified_provider_id = None

    def _record_success(self, provider: dict[str, Any], model: str, usage: dict[str, Any]) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "INSERT INTO audit_log(kind, ref_id, detail_json, created_at) VALUES (?, ?, ?, ?)",
                (
                    "llm_call",
                    f"{provider['id']}:{model}",
                    json_dumps({"provider": provider["id"], "protocol": provider["protocol"], **usage}),
                    iso(),
                ),
            )

    def _record_failure(self, exc: Exception) -> None:
        self._client_error = str(exc)[:500]
        self._verified_provider_id = None
        with self.db.connect() as conn:
            conn.execute(
                "INSERT INTO audit_log(kind, ref_id, detail_json, created_at) VALUES (?, ?, ?, ?)",
                ("llm_error", type(exc).__name__, json_dumps({"error": str(exc)[:500]}), iso()),
            )


def parse_json_object(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        value = json.loads(cleaned)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        pass
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        value = json.loads(cleaned[start : end + 1])
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        return None


def validate_citations(
    citations: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
    vault: Any,
) -> list[dict[str, Any]]:
    """Keep only citations that still resolve to a local note and a known snippet."""
    by_id = {item["citation_id"]: item for item in evidence}
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    seen_notes: set[str] = set()
    for citation in citations or []:
        if not isinstance(citation, dict):
            continue
        cid = str(citation.get("id") or "").strip()
        item = by_id.get(cid)
        if not item or cid in seen or item["note_id"] in seen_notes:
            continue
        note = vault.read_note(item["note_id"])
        if note is None:
            continue
        quote = str(citation.get("quote") or "").strip()
        if quote:
            compact_quote = re.sub(r"\s+", "", quote)
            compact_snippet = re.sub(r"\s+", "", item["snippet"])
            if compact_quote and compact_quote not in compact_snippet:
                continue
        seen.add(cid)
        seen_notes.add(item["note_id"])
        output.append(
            {
                "citation_id": cid,
                "note_id": item["note_id"],
                "title": note["title"],
                "path": note["path"],
                "snippet": item["snippet"][:500],
                "quote": quote[:300],
                "app_url": f"/api/notes/{item['note_id']}",
                "obsidian_uri": note["obsidian_uri"],
            }
        )
    return output
