from __future__ import annotations

import pytest

from knowgraph.github_rankings import GitHubRankingService, _parse_trending


SAMPLE = """
<article class="Box-row">
  <h2 class="h3 lh-condensed">
    <a href="/owner/repo">
      <span>owner / repo</span>
    </a>
  </h2>
  <p class="col-9 color-fg-muted my-1 pr-4">A sample repository for testing.</p>
  <span itemprop="programmingLanguage">C++</span>
  <a href="/owner/repo/stargazers">1,234</a>
  <a href="/owner/repo/forks">56</a>
  <span>89 stars today</span>
</article>
"""


def test_parse_trending_repository():
    repos = _parse_trending(SAMPLE)
    assert len(repos) == 1
    repo = repos[0]
    assert repo["full_name"] == "owner/repo"
    assert repo["description"] == "A sample repository for testing."
    assert repo["language"] == "C++"
    assert repo["stars"] == 1234
    assert repo["forks"] == 56
    assert repo["stars_gained"] == 89


def test_ranking_service_rejects_invalid_mode():
    service = GitHubRankingService()
    with pytest.raises(ValueError):
        service.fetch("unknown", "all")
    with pytest.raises(ValueError):
        service.fetch("stars", "unknown")


class FakeSummaryLLM:
    available = True

    def complete_json(self, system, prompt, **kwargs):
        return {
            "repos": [
                {"full_name": "owner/repo", "summary": "一个用于测试的中文核心功能说明。"}
            ]
        }


def test_ranking_service_adds_chinese_summaries(monkeypatch):
    service = GitHubRankingService(FakeSummaryLLM())
    monkeypatch.setattr(
        service,
        "_trending",
        lambda mode, category, limit: {
            "mode": mode,
            "category": category,
            "mode_label": "今日新增 Star",
            "category_label": "全部",
            "source": "GitHub Trending",
            "note": "test",
            "repos": [{"full_name": "owner/repo", "description": "A test.", "topics": []}],
        },
    )
    result = service.fetch("trending_daily", "all", 1)
    repo = result["repos"][0]
    assert repo["summary_zh"] == "一个用于测试的中文核心功能说明。"
    assert repo["summary_source"] == "ai"


def test_github_ranking_api(client, monkeypatch):
    monkeypatch.setattr(
        client.app.state.services.github_rankings,
        "fetch",
        lambda mode, category, limit: {
            "mode": mode,
            "category": category,
            "mode_label": "本周新增 Star",
            "category_label": "C++",
            "source": "GitHub Trending",
            "note": "test",
            "repos": [{"full_name": "owner/repo", "url": "https://github.com/owner/repo"}],
        },
    )
    response = client.get("/api/github/rankings?mode=trending_weekly&category=cpp")
    assert response.status_code == 200
    assert response.json()["repos"][0]["full_name"] == "owner/repo"


def test_news_items_carry_source_platform(app, candidate):
    source = app.sources.add("rss", "Platform feed", {"url": "https://example.org/feed"}, platform="C++ 博客")
    app.ingest.ingest(candidate, source_id=source["id"], enrich=False)
    item = app.ingest.list_news(days=7)[0]
    assert item["source_platform"] == "C++ 博客"
