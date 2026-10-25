"""Multi-provider model configurations with vendor/platform presets."""

from __future__ import annotations

import re
import uuid
from typing import Any
from urllib.parse import urlsplit

from .config import RuntimeSettings
from .credentials import CredentialStore
from .db import Database, json_dumps, json_loads
from .utils import iso, stable_id

# Presets are configuration starting points, not a claim that a vendor is
# currently reachable or that its model IDs never change. URLs and model IDs
# follow CC-Switch's public provider presets and the platforms' own docs.
PRESETS: list[dict[str, Any]] = [
    {
        "key": "anthropic",
        "name": "Anthropic 官方",
        "family": "anthropic",
        "protocol": "anthropic",
        "auth_style": "x-api-key",
        "base_url": "https://api.anthropic.com",
        "model": "claude-opus-5-5",
        "models": ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"],
        "website_url": "https://www.anthropic.com/claude-code",
        "docs_url": "https://docs.anthropic.com",
        "api_key_url": "https://console.anthropic.com/settings/keys",
        "requires_key": True,
        "note": "官方 Messages API，支持服务端 fallback 与结构化提示。",
    },
    {
        "key": "deepseek",
        "name": "DeepSeek",
        "family": "deepseek",
        "protocol": "anthropic",
        "auth_style": "bearer",
        "base_url": "https://api.deepseek.com/anthropic",
        "model": "deepseek-v4-pro[1M]",
        "models": ["deepseek-v4-pro[1M]", "deepseek-flash[1M]"],
        "website_url": "https://platform.deepseek.com",
        "docs_url": "https://api-docs.deepseek.com",
        "api_key_url": "https://platform.deepseek.com/api_keys",
        "requires_key": True,
        "note": "使用 DeepSeek 的 Anthropic 兼容端点。",
    },
    {
        "key": "kimi",
        "name": "Kimi",
        "family": "kimi",
        "protocol": "anthropic",
        "auth_style": "bearer",
        "base_url": "https://api.moonshot.cn/anthropic",
        "model": "kimi-k2.7-code",
        "models": ["kimi-k2.7-code"],
        "website_url": "https://platform.kimi.com",
        "docs_url": "https://platform.moonshot.cn/docs",
        "api_key_url": "https://platform.moonshot.cn/console/api-keys",
        "requires_key": True,
        "note": "Moonshot 的 Anthropic 兼容端点。",
    },
    {
        "key": "glm",
        "name": "智谱 GLM",
        "family": "zhipu",
        "protocol": "anthropic",
        "auth_style": "bearer",
        "base_url": "https://open.bigmodel.cn/api/anthropic",
        "model": "glm-5.3[1M]",
        "models": ["glm-5.3[1M]"],
        "website_url": "https://open.bigmodel.cn",
        "docs_url": "https://docs.bigmodel.cn",
        "api_key_url": "https://www.bigmodel.cn/claude-code",
        "requires_key": True,
        "note": "智谱开放平台的 Anthropic 兼容端点。",
    },
    {
        "key": "qwen",
        "name": "千问 / 通义",
        "family": "qianwen",
        "protocol": "anthropic",
        "auth_style": "bearer",
        "base_url": "https://dashscope.aliyuncs.com/apps/anthropic",
        "model": "qwen3.8-max[1M]",
        "models": ["qwen3.8-max[1M]", "qwen3.7-plus[1M]", "qwen3.8-flash[1M]"],
        "website_url": "https://platform.qianwenai.com",
        "docs_url": "https://help.aliyun.com/zh/model-studio",
        "api_key_url": "https://platform.qianwenai.com/home/api-keys",
        "requires_key": True,
        "note": "阿里云百炼 / 千问平台的 Anthropic 兼容端点。",
    },
    {
        "key": "mimo",
        "name": "小米 MiMo",
        "family": "xiaomi-mimo",
        "protocol": "anthropic",
        "auth_style": "bearer",
        "base_url": "https://api.xiaomimimo.com/anthropic",
        "model": "mimo-v2.6-pro[1M]",
        "models": ["mimo-v2.6-pro[1M]"],
        "website_url": "https://platform.xiaomimimo.com",
        "docs_url": "https://platform.xiaomimimo.com",
        "api_key_url": "https://platform.xiaomimimo.com/#/console/api-keys",
        "requires_key": True,
        "note": "小米 MiMo 开放平台的 Anthropic 兼容端点。",
    },
    {
        "key": "openai",
        "name": "OpenAI 官方",
        "family": "openai",
        "protocol": "openai",
        "auth_style": "bearer",
        "base_url": "https://api.openai.com/v1",
        "model": "",
        "models": [],
        "website_url": "https://platform.openai.com",
        "docs_url": "https://platform.openai.com/docs",
        "api_key_url": "https://platform.openai.com/api-keys",
        "requires_key": True,
        "note": "使用 OpenAI Chat Completions 协议，请填写账号可用的模型 ID。",
    },
    {
        "key": "micu",
        "name": "Micu",
        "family": "micu",
        "protocol": "anthropic",
        "auth_style": "bearer",
        "base_url": "https://www.micuapi.ai",
        "model": "",
        "models": [],
        "website_url": "https://www.micuapi.ai",
        "docs_url": "",
        "api_key_url": "https://www.micuapi.ai/register?aff=aOYQ",
        "requires_key": True,
        "note": "第三方中转平台；模型 ID 以平台控制台为准。",
    },
    {
        "key": "cctq",
        "name": "CCTQ",
        "family": "cctq",
        "protocol": "anthropic",
        "auth_style": "bearer",
        "base_url": "https://www.cctq.ai",
        "model": "",
        "models": [],
        "website_url": "https://www.cctq.ai",
        "docs_url": "https://doc.cctq.ai/guide/cc-switch-claude-code.html",
        "api_key_url": "https://doc.cctq.ai",
        "requires_key": True,
        "note": "第三方中转平台；模型 ID 以平台控制台为准。",
    },
    {
        "key": "custom",
        "name": "自定义平台",
        "family": "custom",
        "protocol": "anthropic",
        "auth_style": "bearer",
        "base_url": "",
        "model": "",
        "models": [],
        "website_url": "",
        "docs_url": "",
        "api_key_url": "",
        "requires_key": True,
        "note": "手动填写协议、请求地址和模型 ID。",
    },
]

PROTOCOLS = {"anthropic", "openai"}
AUTH_STYLES = {"x-api-key", "bearer"}
DEFAULT_PROVIDER_ID = "anthropic-official"


class ProviderService:
    def __init__(self, db: Database, settings: RuntimeSettings, credentials: CredentialStore) -> None:
        self.db = db
        self.settings = settings
        self.credentials = credentials
        self.ensure_default()

    def presets(self) -> list[dict[str, Any]]:
        return [dict(preset) for preset in PRESETS]

    def ensure_default(self) -> None:
        with self.db.connect() as conn:
            count = conn.execute("SELECT COUNT(*) AS n FROM llm_profiles").fetchone()["n"]
            if count == 0:
                preset = next(item for item in PRESETS if item["key"] == "anthropic")
                model = self.settings.get("model") or preset["model"]
                now = iso()
                conn.execute(
                    """
                    INSERT INTO llm_profiles(id, name, preset_key, family, protocol, auth_style,
                                             base_url, model, models_json, website_url, docs_url,
                                             api_key_url, extra_headers_json, requires_key, active,
                                             created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '{}', 1, 1, ?, ?)
                    """,
                    (
                        DEFAULT_PROVIDER_ID,
                        preset["name"],
                        preset["key"],
                        preset["family"],
                        preset["protocol"],
                        preset["auth_style"],
                        preset["base_url"],
                        model,
                        json_dumps(preset["models"]),
                        preset["website_url"],
                        preset["docs_url"],
                        preset["api_key_url"],
                        now,
                        now,
                    ),
                )
            active = conn.execute("SELECT id FROM llm_profiles WHERE active = 1 LIMIT 1").fetchone()
            if active is None:
                first = conn.execute("SELECT id FROM llm_profiles ORDER BY created_at LIMIT 1").fetchone()
                if first:
                    conn.execute("UPDATE llm_profiles SET active = 1 WHERE id = ?", (first["id"],))
        self.credentials.migrate_legacy(DEFAULT_PROVIDER_ID)

    def list(self) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM llm_profiles ORDER BY active DESC, name COLLATE NOCASE"
            ).fetchall()
        return [self._public(row) for row in rows]

    def get(self, provider_id: str) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM llm_profiles WHERE id = ?", (provider_id,)).fetchone()
        return self._public(row) if row else None

    def active(self) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM llm_profiles WHERE active = 1 LIMIT 1").fetchone()
        return self._public(row) if row else None

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        preset_key = str(payload.get("preset_key") or "").strip()
        preset = next((dict(item) for item in PRESETS if item["key"] == preset_key), {})
        data = _merge(preset, payload)
        data["name"] = str(data.get("name") or "").strip()[:120]
        data["protocol"] = str(data.get("protocol") or "anthropic")
        data["auth_style"] = str(data.get("auth_style") or "bearer")
        data["base_url"] = str(data.get("base_url") or "").strip().rstrip("/")
        data["model"] = str(data.get("model") or "").strip()
        _validate(data)
        api_key = str(payload.get("api_key") or "").strip()
        _validate_optional_key(api_key)
        provider_id = stable_id("prov", uuid.uuid4().hex)
        models = _string_list(data.get("models"))
        headers = _headers(data.get("extra_headers"))
        activate = bool(payload.get("activate"))
        with self.db.connect() as conn:
            any_active = conn.execute("SELECT 1 FROM llm_profiles WHERE active = 1 LIMIT 1").fetchone()
            active = 1 if activate or not any_active else 0
            if active:
                conn.execute("UPDATE llm_profiles SET active = 0")
            now = iso()
            conn.execute(
                """
                INSERT INTO llm_profiles(id, name, preset_key, family, protocol, auth_style,
                                         base_url, model, models_json, website_url, docs_url,
                                         api_key_url, extra_headers_json, requires_key, active,
                                         created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    provider_id,
                    data["name"],
                    preset_key or None,
                    str(data.get("family") or ""),
                    data["protocol"],
                    data["auth_style"],
                    data["base_url"],
                    data["model"],
                    json_dumps(models),
                    str(data.get("website_url") or ""),
                    str(data.get("docs_url") or ""),
                    str(data.get("api_key_url") or ""),
                    json_dumps(headers),
                    1 if data.get("requires_key", True) else 0,
                    active,
                    now,
                    now,
                ),
            )
        api_key = str(payload.get("api_key") or "").strip()
        if api_key:
            self.set_key(provider_id, api_key)
        return self.get(provider_id)  # type: ignore[return-value]

    def update(self, provider_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM llm_profiles WHERE id = ?", (provider_id,)).fetchone()
        if row is None:
            raise KeyError(provider_id)
        data = self._public(row)
        merged = _merge(data, payload)
        merged["name"] = str(merged.get("name") or "").strip()[:120]
        merged["protocol"] = str(merged.get("protocol") or "anthropic")
        merged["auth_style"] = str(merged.get("auth_style") or "bearer")
        merged["base_url"] = str(merged.get("base_url") or "").strip().rstrip("/")
        merged["model"] = str(merged.get("model") or "").strip()
        _validate(merged)
        api_key = str(payload.get("api_key") or "").strip()
        _validate_optional_key(api_key)
        models = _string_list(merged.get("models"))
        headers = _headers(merged.get("extra_headers"))
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE llm_profiles SET name = ?, protocol = ?, auth_style = ?, base_url = ?, model = ?,
                    models_json = ?, website_url = ?, docs_url = ?, api_key_url = ?,
                    extra_headers_json = ?, requires_key = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    merged["name"],
                    merged["protocol"],
                    merged["auth_style"],
                    merged["base_url"],
                    merged["model"],
                    json_dumps(models),
                    str(merged.get("website_url") or ""),
                    str(merged.get("docs_url") or ""),
                    str(merged.get("api_key_url") or ""),
                    json_dumps(headers),
                    1 if merged.get("requires_key", True) else 0,
                    iso(),
                    provider_id,
                ),
            )
        if payload.get("activate"):
            self.activate(provider_id)
        if api_key:
            self.set_key(provider_id, api_key)
        return self.get(provider_id)  # type: ignore[return-value]

    def delete(self, provider_id: str) -> None:
        with self.db.connect() as conn:
            count = conn.execute("SELECT COUNT(*) AS n FROM llm_profiles").fetchone()["n"]
            if count <= 1:
                raise ValueError("At least one model provider must remain")
            row = conn.execute("SELECT * FROM llm_profiles WHERE id = ?", (provider_id,)).fetchone()
            if row is None:
                raise KeyError(provider_id)
            was_active = bool(row["active"])
            conn.execute("DELETE FROM llm_profiles WHERE id = ?", (provider_id,))
            if was_active:
                other = conn.execute("SELECT id FROM llm_profiles ORDER BY created_at LIMIT 1").fetchone()
                if other:
                    conn.execute("UPDATE llm_profiles SET active = 1 WHERE id = ?", (other["id"],))
        self.clear_key(provider_id)

    def activate(self, provider_id: str) -> dict[str, Any]:
        with self.db.connect() as conn:
            row = conn.execute("SELECT id FROM llm_profiles WHERE id = ?", (provider_id,)).fetchone()
            if row is None:
                raise KeyError(provider_id)
            conn.execute("UPDATE llm_profiles SET active = 0")
            conn.execute("UPDATE llm_profiles SET active = 1, updated_at = ? WHERE id = ?", (iso(), provider_id))
        return self.get(provider_id)  # type: ignore[return-value]

    def set_key(self, provider_id: str, api_key: str) -> dict[str, Any]:
        if self.get(provider_id) is None:
            raise KeyError(provider_id)
        self.credentials.set(api_key, scope=provider_id)
        return self.get(provider_id)  # type: ignore[return-value]

    def clear_key(self, provider_id: str) -> dict[str, Any]:
        self.credentials.delete(scope=provider_id)
        return self.get(provider_id) or {}

    def key_for(self, provider: dict[str, Any]) -> str | None:
        return self.credentials.get(provider["id"], provider["protocol"])

    def key_status(self, provider: dict[str, Any]) -> dict[str, Any]:
        source = self.credentials.source(provider["id"], provider["protocol"])
        return {
            "configured": source != "none" or not provider["requires_key"],
            "source": source,
            "hint": self.credentials.hint(provider["id"], provider["protocol"]),
        }

    def _public(self, row: Any) -> dict[str, Any]:
        provider = {
            "id": row["id"],
            "name": row["name"],
            "preset_key": row["preset_key"],
            "family": row["family"],
            "protocol": row["protocol"],
            "auth_style": row["auth_style"],
            "base_url": row["base_url"],
            "model": row["model"],
            "models": json_loads(row["models_json"], []),
            "website_url": row["website_url"],
            "docs_url": row["docs_url"],
            "api_key_url": row["api_key_url"],
            "extra_headers": json_loads(row["extra_headers_json"], {}),
            "requires_key": bool(row["requires_key"]),
            "active": bool(row["active"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        provider.update(self.key_status(provider))
        provider["key_source"] = provider["source"]
        provider["key_hint"] = provider["hint"]
        return provider


def _merge(base: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in payload.items():
        if value is not None and key not in {"api_key", "activate"}:
            merged[key] = value
    return merged


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        value = re.split(r"[,，\n]", value)
    if not isinstance(value, (list, tuple)):
        return []
    output: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text and text not in output:
            output.append(text[:200])
    return output[:100]


def _headers(value: Any) -> dict[str, str]:
    if isinstance(value, str):
        value = json_loads(value, {})
    if not isinstance(value, dict):
        return {}
    return {str(key)[:100]: str(val)[:500] for key, val in value.items()}


def _validate(data: dict[str, Any]) -> None:
    if not data.get("name"):
        raise ValueError("Provider name is empty")
    if data.get("protocol") not in PROTOCOLS:
        raise ValueError("Unsupported protocol")
    if data.get("auth_style") not in AUTH_STYLES:
        raise ValueError("Unsupported auth style")
    if not data.get("model"):
        raise ValueError("Model ID is required")
    parts = urlsplit(str(data.get("base_url") or ""))
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ValueError("Base URL must be a valid http(s) address")
    if data.get("protocol") == "openai" and parts.path.rstrip("/").endswith("/anthropic"):
        raise ValueError(
            "This is an Anthropic-compatible endpoint; select the Anthropic Messages protocol"
        )


def _validate_optional_key(api_key: str) -> None:
    if api_key and (len(api_key) < 8 or any(ch.isspace() for ch in api_key)):
        raise ValueError("API key format is invalid")
