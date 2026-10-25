"""Live GitHub ranking queries for the news workspace."""

from __future__ import annotations

import html
import json
import os
import re
import time
from datetime import timedelta
from typing import Any

import httpx

from .adapters import USER_AGENT, robots_allowed
from .llm import LLMService
from .utils import iso, utcnow

MODES = {
    "trending_daily": {"label": "今日新增 Star", "since": "daily", "days": 1},
    "trending_weekly": {"label": "本周新增 Star", "since": "weekly", "days": 7},
    "trending_monthly": {"label": "本月新增 Star", "since": "monthly", "days": 30},
    "stars": {"label": "总 Star 榜", "since": None, "days": 0},
    "forks": {"label": "Fork 榜", "since": None, "days": 0},
    "new": {"label": "最新优质项目", "since": None, "days": 30},
}

CATEGORIES = {
    "all": {"label": "全部", "query": "", "trending_path": ""},
    "cpp": {"label": "C++", "query": "language:C++", "trending_path": "c++"},
    "qt": {"label": "Qt", "query": "qt in:name,description language:C++", "trending_path": None},
    "python": {"label": "Python", "query": "language:Python", "trending_path": "python"},
    "go": {"label": "Go", "query": "language:Go", "trending_path": "go"},
    "ai": {"label": "AI / LLM", "query": "topic:llm", "trending_path": None},
    "agent": {"label": "AI Agent", "query": "topic:ai-agents", "trending_path": None},
    "ml": {"label": "机器学习", "query": "topic:machine-learning", "trending_path": None},
    "database": {"label": "数据库", "query": "topic:database", "trending_path": None},
    "network": {"label": "网络编程", "query": "topic:networking", "trending_path": None},
}


class GitHubRankingError(RuntimeError):
    pass


class GitHubRankingService:
    def __init__(self, llm: LLMService | None = None, ttl_seconds: int = 600) -> None:
        self.llm = llm
        self.ttl = ttl_seconds
        self._cache: dict[tuple[str, str, int], tuple[float, dict[str, Any]]] = {}

    def fetch(self, mode: str, category: str, limit: int = 12) -> dict[str, Any]:
        if mode not in MODES:
            raise ValueError("Unsupported ranking mode")
        if category not in CATEGORIES:
            raise ValueError("Unsupported GitHub category")
        limit = max(1, min(30, int(limit)))
        cache_key = (mode, category, limit)
        cached = self._cache.get(cache_key)
        if cached and cached[0] > time.time():
            return cached[1]
        if mode.startswith("trending_") and CATEGORIES[category]["trending_path"] is not None:
            result = self._trending(mode, category, limit)
        else:
            result = self._search(mode, category, limit)
        self._summarize(result["repos"])
        result["fetched_at"] = iso()
        self._cache[cache_key] = (time.time() + self.ttl, result)
        return result

    def _summarize(self, repos: list[dict[str, Any]]) -> None:
        for repo in repos:
            repo["summary_zh"] = ""
            repo["summary_source"] = "original"
        if not repos or self.llm is None or not self.llm.available:
            return
        payload = [
            {
                "full_name": repo.get("full_name", ""),
                "description": repo.get("description", ""),
                "language": repo.get("language", ""),
                "topics": (repo.get("topics") or [])[:10],
                "stars": repo.get("stars"),
            }
            for repo in repos
        ]
        try:
            result = self.llm.complete_json(
                "你是技术编辑。为每个 GitHub 仓库写一句中文核心功能说明，帮助读者一眼判断它是什么、"
                "解决什么问题、适合谁。只依据仓库名、描述、语言和 topics，不夸大、不编造功能。"
                "不要重复星标数。输出严格 JSON："
                '{"repos":[{"full_name":"...","summary":"不超过 70 字"}]}。',
                "仓库列表：\n" + json.dumps(payload, ensure_ascii=False),
                max_tokens=3000,
                effort="low",
            )
        except Exception:
            return
        summaries = {
            str(item.get("full_name") or ""): str(item.get("summary") or "").strip()
            for item in (result or {}).get("repos", [])
            if isinstance(item, dict)
        }
        for repo in repos:
            summary = summaries.get(repo.get("full_name", ""), "")
            if summary:
                repo["summary_zh"] = re.sub(r"\s+", " ", summary)[:180]
                repo["summary_source"] = "ai"

    def _trending(self, mode: str, category: str, limit: int) -> dict[str, Any]:
        since = MODES[mode]["since"]
        path = CATEGORIES[category]["trending_path"] or ""
        url = f"https://github.com/trending/{path}?since={since}" if path else f"https://github.com/trending?since={since}"
        if not robots_allowed(url):
            raise GitHubRankingError("GitHub robots.txt disallows the trending page.")
        with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30.0, follow_redirects=True) as client:
            response = client.get(url)
        if response.status_code != 200:
            raise GitHubRankingError(f"GitHub trending returned HTTP {response.status_code}")
        repos = _parse_trending(response.text)[:limit]
        return {
            "mode": mode,
            "category": category,
            "mode_label": MODES[mode]["label"],
            "category_label": CATEGORIES[category]["label"],
            "source": "GitHub Trending",
            "note": f"GitHub Trending · {since}，按新增 Star 排序。",
            "repos": repos,
        }

    def _search(self, mode: str, category: str, limit: int) -> dict[str, Any]:
        query = CATEGORIES[category]["query"]
        note = "按总 Star 排序。"
        sort = "stars"
        if mode == "forks":
            sort = "forks"
            note = "按 Fork 数排序。"
        elif mode == "new":
            cutoff = (utcnow() - timedelta(days=30)).date().isoformat()
            query = f"{query} created:>={cutoff} stars:>=50".strip()
            note = "最近 30 天创建、Star 不少于 50 的项目，按 Star 排序。"
        elif mode.startswith("trending_"):
            cutoff = (utcnow() - timedelta(days=MODES[mode]["days"])).date().isoformat()
            query = f"{query} pushed:>={cutoff} stars:>=50".strip()
            note = "GitHub Search 不支持直接按新增 Star 排序；此榜单按近期活跃与总 Star 估算。"
        else:
            query = f"{query} stars:>100".strip()

        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": USER_AGENT,
        }
        token = os.getenv("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        params = {"q": query, "sort": sort, "order": "desc", "per_page": limit}
        with httpx.Client(headers=headers, timeout=30.0, follow_redirects=True) as client:
            response = client.get("https://api.github.com/search/repositories", params=params)
        if response.status_code in {403, 429}:
            raise GitHubRankingError("GitHub API rate limit reached. Configure GITHUB_TOKEN or retry later.")
        if response.status_code != 200:
            raise GitHubRankingError(f"GitHub search returned HTTP {response.status_code}")
        data = response.json()
        repos = [_search_repo(item) for item in data.get("items", [])[:limit]]
        return {
            "mode": mode,
            "category": category,
            "mode_label": MODES[mode]["label"],
            "category_label": CATEGORIES[category]["label"],
            "source": "GitHub Search",
            "note": note,
            "repos": repos,
        }


def _parse_trending(raw: str) -> list[dict[str, Any]]:
    repos: list[dict[str, Any]] = []
    blocks = raw.split('<article class="Box-row">')[1:]
    for block in blocks:
        match = re.search(r'<h2[^>]*>.*?href="/([^"?]+)"', block, re.S)
        if not match:
            continue
        full_name = match.group(1).strip().strip("/")
        if full_name.count("/") != 1:
            continue
        description = ""
        desc_match = re.search(r'<p[^>]*class="[^"]*col-9[^"]*"[^>]*>(.*?)</p>', block, re.S)
        if desc_match:
            description = _clean(desc_match.group(1))
        language_match = re.search(r'itemprop="programmingLanguage">([^<]+)<', block)
        stars_match = re.search(r"([\d,]+)\s+stars\s+(today|this week|this month)", block)
        stars_gained = _number(stars_match.group(1)) if stars_match else None
        stars = _metric(block, "stargazers")
        forks = _metric(block, "forks")
        repos.append(
            {
                "full_name": full_name,
                "url": f"https://github.com/{full_name}",
                "description": description,
                "language": html.unescape(language_match.group(1).strip()) if language_match else "",
                "stars": stars,
                "forks": forks,
                "stars_gained": stars_gained,
                "topics": [],
                "owner": full_name.split("/", 1)[0],
                "avatar_url": "",
                "pushed_at": None,
                "created_at": None,
            }
        )
    return repos


def _search_repo(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "full_name": item.get("full_name", ""),
        "url": item.get("html_url", ""),
        "description": item.get("description") or "",
        "language": item.get("language") or "",
        "stars": item.get("stargazers_count"),
        "forks": item.get("forks_count"),
        "stars_gained": None,
        "topics": item.get("topics") or [],
        "owner": (item.get("owner") or {}).get("login", ""),
        "avatar_url": (item.get("owner") or {}).get("avatar_url", ""),
        "pushed_at": item.get("pushed_at"),
        "created_at": item.get("created_at"),
    }


def _metric(block: str, segment: str) -> int | None:
    match = re.search(rf'href="/[^"]+/{segment}"[^>]*>(.*?)</a>', block, re.S)
    if not match:
        return None
    return _number(_clean(match.group(1)))


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", value))).strip()


def _number(value: str) -> int | None:
    digits = re.sub(r"[^\d]", "", value or "")
    return int(digits) if digits else None
