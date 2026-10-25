from __future__ import annotations


def test_api_profile_and_search(client):
    response = client.patch("/api/profile", json={"name": "Ada", "headline": "Backend engineer"})
    assert response.status_code == 200
    assert response.json()["name"] == "Ada"
    assert client.post("/api/skills", json={"name": "Python", "level": 3}).status_code == 200
    goal = client.post(
        "/api/goals", json={"kind": "short", "title": "掌握分布式事务"}
    )
    assert goal.status_code == 200
    profile = client.get("/api/profile").json()
    assert profile["skills"][0]["name"] == "Python"
    assert profile["goals"][0]["title"] == "掌握分布式事务"


def test_api_source_and_settings(client):
    response = client.post(
        "/api/sources",
        json={
            "kind": "arxiv",
            "name": "arXiv CS",
            "config": {"categories": ["cs.AI"]},
            "interval_minutes": 1440,
        },
    )
    assert response.status_code == 200
    sources = client.get("/api/sources").json()["sources"]
    assert len(sources) == 1
    invalid = client.post(
        "/api/sources",
        json={"kind": "rss", "name": "bad", "config": {"url": "file:///etc/passwd"}},
    )
    assert invalid.status_code == 400
    settings = client.patch("/api/settings", json={"timezone": "UTC", "collection_limit": 5})
    assert settings.status_code == 200
    assert settings.json()["settings"]["collection_limit"] == "5"
    assert client.patch("/api/settings", json={"collection_limit": "many"}).status_code == 400


def test_api_cross_origin_write_blocked(client):
    response = client.patch(
        "/api/profile",
        json={"name": "Eve"},
        headers={"Origin": "https://evil.example"},
    )
    assert response.status_code == 403


def test_api_ask_and_review(client):
    answer = client.post("/api/ask", json={"question": "本地没有的问题", "mode": "local"})
    assert answer.status_code == 200
    assert answer.json()["mode"] == "insufficient"
    review = client.post("/api/reviews/generate", json={"period_type": "week", "offset": -1})
    assert review.status_code == 200
    assert review.json()["vault_path"].startswith("50-Reviews/")


def test_api_note_not_found(client):
    assert client.get("/api/notes/note_missing").status_code == 404


def test_api_health(client):
    data = client.get("/api/health").json()
    assert data["app"] == "knowgraph"
    assert "vault" in data


def test_api_resume_file_upload(client):
    response = client.post(
        "/api/profile/resume-file",
        files={"file": ("resume.md", "Python and Redis experience.", "text/markdown")},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["profile"]["resume_filename"] == "resume.md"
    assert "Python" in data["profile"]["resume_text"]
    assert any(item["name"] == "Python" for item in data["suggestions"])


def test_api_resume_file_rejects_unknown_type(client):
    response = client.post(
        "/api/profile/resume-file",
        files={"file": ("resume.exe", b"binary", "application/octet-stream")},
    )
    assert response.status_code == 400


def test_review_api_returns_local_period_dates(client):
    generated = client.post("/api/reviews/generate", json={"period_type": "week", "offset": -1}).json()
    listed = client.get("/api/reviews").json()["reviews"]
    assert listed
    first = listed[0]
    assert first["label"].startswith("20")
    assert first["period_start_local"] <= first["period_end_local"]
    detail = client.get(f"/api/reviews/{generated['id']}").json()
    assert detail["period_start_local"] == first["period_start_local"]
