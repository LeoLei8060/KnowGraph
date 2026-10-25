from __future__ import annotations

from pathlib import Path

from knowgraph.credentials import ANTHROPIC_ENV, CredentialStore


def test_credential_store_roundtrip(tmp_path: Path, monkeypatch):
    monkeypatch.delenv(ANTHROPIC_ENV, raising=False)
    store = CredentialStore(tmp_path, disable_keyring=True)
    assert store.get() is None
    store.set("test-key-1234567890")
    assert store.get() == "test-key-1234567890"
    assert store.source() == "store"
    assert store.hint().endswith("7890")
    assert (tmp_path / "secrets" / "anthropic.key").exists()
    store.delete()
    assert store.get() is None
    assert store.source() == "none"


def test_api_key_storage_does_not_leak(client, monkeypatch):
    monkeypatch.delenv(ANTHROPIC_ENV, raising=False)
    key = "test-key-secret-1234"
    response = client.post("/api/settings/llm-key", json={"api_key": key})
    assert response.status_code == 200
    data = response.json()
    assert data["configured"] is True
    assert data["key_hint"].endswith("1234")
    assert key not in str(client.get("/api/settings").json())
    cleared = client.delete("/api/settings/llm-key").json()
    assert cleared["configured"] is False


def test_api_key_test_endpoint(client, monkeypatch):
    monkeypatch.setattr(
        client.app.state.services.llm,
        "check_connection",
        lambda: {"ok": True, "model": "claude-opus-5-5", "input_tokens": 5},
    )
    response = client.post("/api/settings/llm-test")
    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_invalid_key_is_rejected(client):
    response = client.post("/api/settings/llm-key", json={"api_key": "short"})
    assert response.status_code == 422
