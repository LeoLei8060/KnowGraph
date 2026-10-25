from __future__ import annotations

import pytest


def test_requested_provider_presets_are_present(app):
    keys = {preset["key"] for preset in app.providers.presets()}
    assert {
        "anthropic", "deepseek", "kimi", "glm", "qwen", "mimo",
        "openai", "micu", "cctq", "custom",
    } <= keys
    deepseek = next(preset for preset in app.providers.presets() if preset["key"] == "deepseek")
    assert deepseek["base_url"] == "https://api.deepseek.com/anthropic"
    assert deepseek["protocol"] == "anthropic"
    assert deepseek["auth_style"] == "bearer"


def test_provider_crud_keys_and_active_switch(app):
    original = app.providers.active()
    assert original is not None
    created = app.providers.create(
        {
            "preset_key": "deepseek",
            "name": "DeepSeek 工作",
            "api_key": "test-key-deepseek-1234",
            "activate": True,
        }
    )
    assert created["active"] is True
    assert created["configured"] is True
    assert created["key_hint"].endswith("1234")
    assert "api_key" not in created
    assert app.providers.active()["id"] == created["id"]

    updated = app.providers.update(
        created["id"],
        {"model": "deepseek-chat", "base_url": "https://example.com/anthropic"},
    )
    assert updated["model"] == "deepseek-chat"
    assert updated["base_url"] == "https://example.com/anthropic"
    with pytest.raises(ValueError):
        app.providers.update(created["id"], {"protocol": "openai"})

    app.providers.delete(created["id"])
    assert app.providers.active()["id"] == original["id"]
    with pytest.raises(ValueError):
        app.providers.delete(original["id"])


def test_api_provider_crud_and_no_key_leak(client, monkeypatch):
    presets = client.get("/api/llm/presets").json()["presets"]
    assert any(preset["key"] == "cctq" for preset in presets)
    response = client.post(
        "/api/llm/providers",
        json={
            "preset_key": "glm",
            "name": "GLM 测试",
            "api_key": "test-key-glm-9876",
            "activate": False,
        },
    )
    assert response.status_code == 200
    provider = response.json()
    assert provider["key_hint"].endswith("9876")
    provider_id = provider["id"]
    assert "test-key-glm-9876" not in str(client.get("/api/llm/providers").json())
    assert client.post(f"/api/llm/providers/{provider_id}/activate").status_code == 200
    cleared = client.delete(f"/api/llm/providers/{provider_id}/key")
    assert cleared.status_code == 200
    assert cleared.json()["configured"] is False
    client.post(
        f"/api/llm/providers/{provider_id}/key",
        json={"api_key": "test-key-glm-9876"},
    )
    monkeypatch.setattr(
        client.app.state.services.llm,
        "check_connection",
        lambda provider_id=None: {"ok": True, "provider_name": "GLM 测试", "model": "glm-5.3[1M]"},
    )
    assert client.post(f"/api/llm/providers/{provider_id}/test").status_code == 200
    assert client.delete(f"/api/llm/providers/{provider_id}").status_code == 200


def test_openai_endpoint_construction(app):
    service = app.llm
    assert service._openai_url({"base_url": "https://api.openai.com/v1"}, "chat/completions") == (
        "https://api.openai.com/v1/chat/completions"
    )
    assert service._openai_url({"base_url": "https://api.openai.com/v1/"}, "models") == (
        "https://api.openai.com/v1/models"
    )
