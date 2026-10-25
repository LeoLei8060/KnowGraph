from __future__ import annotations

from knowgraph.adapters import RawCandidate


class FakeOrganizerLLM:
    available = True

    def complete_json(self, system, prompt, **kwargs):
        return {
            "title_zh": "Redis 事务实践",
            "summary": "介绍 Redis 事务与重试时的幂等处理。",
            "tags": ["Redis", "事务"],
            "language": "en",
        }


def test_news_listing_and_detail(app, candidate):
    item_id = app.ingest.ingest(candidate, enrich=False)["id"]
    news = app.ingest.list_news(days=7)
    assert any(item["id"] == item_id for item in news)
    detail = app.ingest.get_item(item_id)
    assert detail["content_text"]


def test_ai_organization_translates_and_persists(app, candidate):
    app.ingest.llm = FakeOrganizerLLM()
    item_id = app.ingest.ingest(candidate, enrich=True)["id"]
    item = app.ingest.get_item(item_id)
    assert item["title_zh"] == "Redis 事务实践"
    assert item["language"] == "en"
    assert item["organization"] == "ai"
    assert "幂等" in item["summary"] or "重试" in item["summary"]
    note = app.vault.read_note(item_id)
    assert "原标题" in note["content"]
    assert "中文概括" in note["content"]


def test_dedicated_source_topics_are_relevant(app, monkeypatch):
    source = app.sources.add(
        "rss",
        "Dedicated C++",
        {
            "url": "https://example.org/feed.xml",
            "topics": ["C++"],
            "limit": 2,
            "enrich_limit": 0,
            "fetch_full_text": False,
        },
    )
    candidates = [
        RawCandidate(
            source_key=f"dedicated:{index}",
            source_type="article",
            title=f"An article {index}",
            url=f"https://example.org/{index}",
            summary="No profile keywords here.",
            content="No profile keywords here.",
        )
        for index in range(5)
    ]
    monkeypatch.setattr("knowgraph.ingest.fetch_source", lambda source, context: candidates)
    result = app.ingest.collect_source(source)
    assert result["saved"] == 2
    items = app.ingest.list_items()
    assert all("来源主题" in item["why"] for item in items)


def test_news_api(client, app, candidate):
    app.ingest.ingest(candidate, enrich=False)
    response = client.get("/api/news?days=7")
    assert response.status_code == 200
    assert any(item["id"] for item in response.json()["items"])
    item_id = response.json()["items"][0]["id"]
    detail = client.get(f"/api/items/{item_id}")
    assert detail.status_code == 200
    assert "content_text" in detail.json()
