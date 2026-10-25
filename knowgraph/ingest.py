"""Collection planning, candidate ranking, de-duplication and Inbox persistence."""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Any

from .adapters import RawCandidate, fetch_article_text, fetch_source
from .config import RuntimeSettings
from .db import Database, json_dumps, json_loads
from .interests import InterestService
from .llm import LLMService
from .profile import ProfileService
from .tags import STOPWORDS, TagService, normalize_tag
from .utils import first_sentences, iso, parse_iso, sha256_text, stable_id, strip_tags, unique, utcnow
from .vault import Vault

SOURCE_TYPES = {
    "rss": "article",
    "web": "article",
    "github": "repository",
    "arxiv": "paper",
}


class SourceService:
    def __init__(self, db: Database, profile: ProfileService, interests: InterestService) -> None:
        self.db = db
        self.profile = profile
        self.interests = interests

    def list(self) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute("SELECT * FROM sources ORDER BY created_at").fetchall()
        return [_source_row(row) for row in rows]

    def get(self, source_id: str) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
        return _source_row(row) if row else None

    def add(
        self,
        kind: str,
        name: str,
        config: dict[str, Any],
        interval_minutes: int = 1440,
        enabled: bool = True,
        platform: str = "",
    ) -> dict[str, Any]:
        if kind not in SOURCE_TYPES:
            raise ValueError(f"Unsupported source kind: {kind}")
        if not name.strip():
            raise ValueError("Source name is empty")
        source_id = stable_id("source", f"{kind}:{name}:{json_dumps(config)}")
        with self.db.connect() as conn:
            existing = conn.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
            if existing:
                return _source_row(existing)
            conn.execute(
                "INSERT INTO sources(id, kind, name, platform, config_json, enabled, interval_minutes, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    source_id,
                    kind,
                    name.strip()[:120],
                    platform.strip()[:80],
                    json_dumps(config),
                    int(enabled),
                    max(5, int(interval_minutes)),
                    iso(),
                ),
            )
            row = conn.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
        return _source_row(row)

    def update(self, source_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
            if row is None:
                raise KeyError(source_id)
            fields = []
            values: list[Any] = []
            if "name" in patch:
                fields.append("name = ?")
                values.append(str(patch["name"]).strip()[:120])
            if "platform" in patch:
                fields.append("platform = ?")
                values.append(str(patch["platform"]).strip()[:80])
            if "config" in patch:
                fields.append("config_json = ?")
                values.append(json_dumps(patch["config"] or {}))
            if "enabled" in patch:
                fields.append("enabled = ?")
                values.append(int(bool(patch["enabled"])))
            if "interval_minutes" in patch:
                fields.append("interval_minutes = ?")
                values.append(max(5, int(patch["interval_minutes"])))
            if fields:
                values.append(source_id)
                conn.execute(f"UPDATE sources SET {', '.join(fields)} WHERE id = ?", values)
            row = conn.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
        return _source_row(row)

    def delete(self, source_id: str) -> None:
        with self.db.connect() as conn:
            conn.execute("DELETE FROM sources WHERE id = ?", (source_id,))

    def plan(self) -> dict[str, Any]:
        stable_terms = self.profile.collection_terms()
        interests = [item for item in self.interests.top(5)]
        sources = self.list()
        descriptions = []
        for source in sources:
            config = source["config"]
            if source["kind"] == "rss":
                target = config.get("url", "")
            elif source["kind"] == "github":
                target = ", ".join(config.get("repos") or [])
            elif source["kind"] == "arxiv":
                target = ", ".join(config.get("categories") or []) or config.get("query", "")
            else:
                target = ", ".join(config.get("urls") or []) or config.get("url", "")
            descriptions.append(
                {
                    "id": source["id"],
                    "name": source["name"],
                    "kind": source["kind"],
                    "target": target,
                    "enabled": source["enabled"],
                    "interval_minutes": source["interval_minutes"],
                }
            )
        return {
            "stable_terms": stable_terms[:20],
            "interest_terms": [item["label"] for item in interests],
            "interest_limit_share": 0.25,
            "collection_limit": self._limit(),
            "sources": descriptions,
        }

    def _limit(self) -> int:
        with self.db.connect() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = 'collection_limit'").fetchone()
        try:
            return max(1, int(row["value"])) if row else 12
        except (TypeError, ValueError):
            return 12


class IngestService:
    def __init__(
        self,
        db: Database,
        settings: RuntimeSettings,
        vault: Vault,
        tags: TagService,
        profile: ProfileService,
        interests: InterestService,
        llm: LLMService,
    ) -> None:
        self.db = db
        self.settings = settings
        self.vault = vault
        self.tags = tags
        self.profile = profile
        self.interests = interests
        self.llm = llm
        self.sources = SourceService(db, profile, interests)

    def context(self) -> dict[str, Any]:
        return {
            "allow_private_urls": self.settings.get_bool("allow_private_urls"),
            "per_source_limit": max(10, self.settings.get_int("collection_limit", 12) * 3),
            "keywords": self.profile.collection_terms(),
        }

    def collect_source(self, source: dict[str, Any]) -> dict[str, Any]:
        context = self.context()
        config = source.get("config") or {}
        source_topics = [str(item) for item in (config.get("topics") or []) if str(item).strip()]
        source_keywords = [str(item) for item in (config.get("keywords") or []) if str(item).strip()]
        try:
            candidates = fetch_source(source, context)
            limit = max(1, int(config.get("limit") or self._limit()))
            selected = self._select(candidates, source_topics, source_keywords, limit=limit)
            fetch_full = bool(config.get("fetch_full_text", True))
            enrich_limit = max(0, int(config.get("enrich_limit", len(selected))))
            saved = 0
            duplicates = 0
            enriched = 0
            for index, candidate in enumerate(selected):
                if fetch_full:
                    self._hydrate_full_text(candidate, config)
                outcome = self.ingest(
                    candidate,
                    source_id=source["id"],
                    enrich=index < enrich_limit,
                    source_topics=source_topics,
                    source_keywords=source_keywords,
                )
                if outcome["created"] or outcome["updated"]:
                    saved += 1
                    if outcome.get("enriched"):
                        enriched += 1
                else:
                    duplicates += 1
            with self.db.connect() as conn:
                conn.execute(
                    "UPDATE sources SET last_run_at = ?, last_status = ?, last_error = NULL WHERE id = ?",
                    (iso(), "ok", source["id"]),
                )
            return {
                "source_id": source["id"],
                "name": source["name"],
                "fetched": len(candidates),
                "saved": saved,
                "duplicates": duplicates,
            }
        except Exception as exc:
            with self.db.connect() as conn:
                conn.execute(
                    "UPDATE sources SET last_run_at = ?, last_status = ?, last_error = ? WHERE id = ?",
                    (iso(), "error", str(exc)[:500], source["id"]),
                )
            raise

    def collect_all(self) -> dict[str, Any]:
        results = []
        errors = []
        for source in self.sources.list():
            if not source["enabled"]:
                continue
            try:
                results.append(self.collect_source(source))
            except Exception as exc:
                errors.append({"source_id": source["id"], "name": source["name"], "error": str(exc)})
        return {"results": results, "errors": errors}

    def _hydrate_full_text(self, candidate: RawCandidate, config: dict[str, Any]) -> None:
        if candidate.source_type not in {"article", "document"}:
            return
        if candidate.content_scope == "full" or not candidate.url:
            return
        try:
            title, text, meta = fetch_article_text(
                candidate.url,
                allow_private=self.settings.get_bool("allow_private_urls"),
                trust_env=not config.get("no_proxy"),
            )
        except Exception:
            return
        if len(text) < 500:
            return
        candidate.content = text
        candidate.content_scope = "full"
        if not candidate.summary:
            candidate.summary = meta.get("description") or meta.get("og:description") or text[:500]
        if not candidate.author:
            candidate.author = meta.get("author", "")
        candidate.raw_meta["full_text_fetched"] = True

    def ingest(
        self,
        candidate: RawCandidate,
        *,
        source_id: str | None = None,
        enrich: bool = True,
        source_topics: list[str] | None = None,
        source_keywords: list[str] | None = None,
    ) -> dict[str, Any]:
        content_hash = sha256_text(
            "\n".join(
                [
                    candidate.title,
                    candidate.summary,
                    candidate.content,
                    candidate.url,
                    json_dumps(candidate.raw_meta),
                ]
            )
        )
        with self.db.connect() as conn:
            existing = conn.execute(
                "SELECT * FROM items WHERE source_key = ?", (candidate.source_key,)
            ).fetchone()
        if existing:
            if existing["content_hash"] == content_hash:
                with self.db.connect() as conn:
                    conn.execute(
                        "UPDATE items SET updated_at = ? WHERE id = ?", (iso(), existing["id"])
                    )
                return {"created": False, "updated": False, "id": existing["id"], "enriched": False}
            self._update_item(existing, candidate, content_hash, source_id, source_topics, source_keywords)
            return {"created": False, "updated": True, "id": existing["id"], "enriched": False}

        item_id = stable_id("src", candidate.source_key)
        matched = self._matches(candidate, source_topics, source_keywords)
        if enrich:
            title_zh, summary, tags, used_llm, language = self._enrich(candidate, matched)
        else:
            title_zh, summary, tags, used_llm, language = self._fallback_organize(candidate, matched)
        tags = unique([normalize_tag(tag) for tag in [*candidate.tags, *tags] if normalize_tag(tag)])[:12]
        for tag in tags:
            try:
                self.tags.register(tag)
            except ValueError:
                continue
        why = self._why(matched)
        fetched_at = iso()
        content_text = (candidate.content or candidate.summary)[:50000]
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO items(id, source_key, source_id, source_type, canonical_url, title, title_zh,
                                  author, published_at, fetched_at, content_scope, summary, excerpt,
                                  content_text, language, organization, organized_at, content_hash,
                                  tags_json, matched_json, why, status, raw_meta_json, first_seen_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'inbox', ?, ?, ?)
                """,
                (
                    item_id,
                    candidate.source_key,
                    source_id,
                    candidate.source_type,
                    candidate.url,
                    _safe_external(candidate.title, 300),
                    _safe_external(title_zh, 300),
                    candidate.author[:200],
                    candidate.published_at,
                    fetched_at,
                    candidate.content_scope,
                    summary,
                    (candidate.content or candidate.summary)[:6000],
                    content_text,
                    language,
                    "ai" if used_llm else "fallback",
                    fetched_at,
                    content_hash,
                    json_dumps(tags),
                    json_dumps(matched),
                    why,
                    json_dumps(candidate.raw_meta),
                    fetched_at,
                    fetched_at,
                ),
            )
        written = self._write_card(
            item_id,
            candidate,
            summary,
            tags,
            why,
            fetched_at,
            status="inbox",
            title_zh=title_zh,
            language=language,
        )
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE items SET vault_path = ? WHERE id = ?", (written["path"], item_id)
            )
        return {"created": True, "updated": False, "id": item_id, "enriched": used_llm}

    def _update_item(
        self,
        existing: Any,
        candidate: RawCandidate,
        content_hash: str,
        source_id: str | None,
        source_topics: list[str] | None = None,
        source_keywords: list[str] | None = None,
        enrich: bool = True,
    ) -> None:
        matched = self._matches(candidate, source_topics, source_keywords)
        if enrich:
            title_zh, summary, new_tags, used_llm, language = self._enrich(candidate, matched)
        else:
            title_zh, summary, new_tags, used_llm, language = self._fallback_organize(candidate, matched)
        tags = unique(
            [
                *json_loads(existing["tags_json"], []),
                *[normalize_tag(tag) for tag in candidate.tags if normalize_tag(tag)],
                *[normalize_tag(tag) for tag in new_tags if normalize_tag(tag)],
            ]
        )[:12]
        content_text = (candidate.content or candidate.summary)[:50000]
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE items SET title = ?, title_zh = ?, summary = ?, excerpt = ?, content_text = ?,
                                 language = ?, organization = ?, organized_at = ?, content_hash = ?,
                                 tags_json = ?, matched_json = ?, why = ?, fetched_at = ?, updated_at = ?,
                                 source_id = COALESCE(?, source_id), raw_meta_json = ?
                WHERE id = ?
                """,
                (
                    _safe_external(candidate.title, 300),
                    _safe_external(title_zh, 300),
                    summary,
                    (candidate.content or candidate.summary)[:6000],
                    content_text,
                    language,
                    "ai" if used_llm else "fallback",
                    iso(),
                    content_hash,
                    json_dumps(tags),
                    json_dumps(matched),
                    self._why(matched),
                    iso(),
                    iso(),
                    source_id,
                    json_dumps(candidate.raw_meta),
                    existing["id"],
                ),
            )
            conn.execute(
                "INSERT INTO item_versions(item_id, content_hash, fetched_at, vault_path, note) "
                "VALUES (?, ?, ?, ?, ?)",
                (existing["id"], content_hash, iso(), existing["vault_path"], "source content updated"),
            )
        if existing["status"] in {"inbox", "read"}:
            written = self._write_card(
                existing["id"],
                candidate,
                summary,
                tags,
                self._why(matched),
                iso(),
                status=existing["status"],
                title_zh=title_zh,
                language=language,
            )
            with self.db.connect() as conn:
                conn.execute(
                    "UPDATE items SET vault_path = ? WHERE id = ?", (written["path"], existing["id"])
                )

    def _write_card(
        self,
        item_id: str,
        candidate: RawCandidate,
        summary: str,
        tags: list[str],
        why: str,
        fetched_at: str,
        status: str,
        title_zh: str = "",
        language: str = "",
    ) -> dict[str, Any]:
        title = _safe_external(candidate.title, 300)
        display_title = _safe_external(title_zh, 300) or title
        summary_safe = _safe_external(summary, 1600)
        body_parts = [f"# {display_title}", ""]
        if title_zh and display_title != title:
            body_parts.extend([f"> 原标题：{title}", ""])
        body_parts.extend(
            [
                "## 推荐原因",
                _wikilink_safe(why) or "暂无画像匹配；按来源顺序收录。",
                "",
                "## 中文概括",
                summary_safe or "（暂无摘要）",
                "",
            ]
        )
        if candidate.content_scope == "full" and candidate.content:
            full_text = _safe_external(candidate.content, 20000, multiline=True)
            body_parts.extend(["## 原文", "> " + full_text.replace("\n", "\n> "), ""])
        elif candidate.content:
            excerpt = _safe_external(candidate.content, 1800, multiline=True)
            body_parts.extend(["## 摘录", "> " + excerpt.replace("\n", "\n> "), ""])
        if tags:
            body_parts.extend(["## 标签", *[f"- `{tag}`" for tag in tags], ""])
        related = self._related_notes(item_id, tags)
        if related:
            body_parts.extend(
                [
                    "## 相关笔记",
                    *[
                        f"- [[{_wikilink_path(note['path'])}|{_wikilink_safe(note['title'])}]]"
                        for note in related
                    ],
                    "",
                ]
            )
        body_parts.extend(
            [
                "## 来源",
                f"[打开原文]({_safe_url(candidate.url)})"
                + (f" · {_safe_external(candidate.author, 120)}" if candidate.author else "")
                + (f" · {candidate.published_at[:10]}" if candidate.published_at else ""),
                "",
                f"> 采集时间 {fetched_at} · 保存范围 {candidate.content_scope}",
            ]
        )
        frontmatter = {
            "id": item_id,
            "item_id": item_id,
            "source_key": candidate.source_key,
            "source_type": candidate.source_type,
            "canonical_url": candidate.url,
            "content_scope": candidate.content_scope,
            "published_at": candidate.published_at,
            "fetched_at": fetched_at,
            "title_zh": display_title,
            "language": language,
            "related_ids": [note["note_id"] for note in related],
        }
        written = self.vault.write_note(
            note_id=item_id,
            kind="source",
            title=display_title,
            body="\n".join(body_parts),
            frontmatter=frontmatter,
            subdir="00-Inbox" if status in {"inbox", "read"} else "10-Sources",
            status=status,
            tags=tags,
            on_conflict="revision",
        )
        return written

    def _matches(
        self,
        candidate: RawCandidate,
        source_topics: list[str] | None = None,
        source_keywords: list[str] | None = None,
    ) -> list[str]:
        headline = " ".join(
            [candidate.title, candidate.summary, " ".join(candidate.tags)]
        ).lower()
        haystack = " ".join([headline, candidate.content]).lower()
        tag_set = {normalize_tag(tag) for tag in candidate.tags}
        matched: list[str] = []
        for topic in source_topics or []:
            if str(topic).strip():
                matched.append(f"来源主题:{str(topic).strip()}")
        for keyword in source_keywords or []:
            # General feeds are filtered on their headline/summary, not on the
            # full article body, which often mentions an unrelated keyword once.
            if _contains_term(headline, keyword) or normalize_tag(keyword) in tag_set:
                matched.append(f"来源关键词:{str(keyword).strip()}")
        for term in self.profile.collection_terms():
            if _contains_term(haystack, term) or normalize_tag(term) in tag_set:
                matched.append(term)
        for interest in self.interests.top(5):
            if interest["blocked"]:
                continue
            if _contains_term(haystack, interest["label"]) or interest["slug"] in tag_set:
                matched.append(f"兴趣:{interest['label']}")
        return unique(matched)[:12]

    def _score(self, candidate: RawCandidate, matched: list[str]) -> float:
        score = len(matched) * 10.0
        if any(match.startswith("兴趣:") for match in matched):
            score += 3.0
        published = parse_iso(candidate.published_at)
        if published and published >= utcnow() - timedelta(days=14):
            score += 4.0
        if candidate.source_type == "paper":
            score += 1.0
        return score

    def _rank(
        self,
        candidates: list[RawCandidate],
        source_topics: list[str] | None = None,
        source_keywords: list[str] | None = None,
    ) -> list[RawCandidate]:
        return sorted(
            candidates,
            key=lambda candidate: (
                self._score(candidate, self._matches(candidate, source_topics, source_keywords)),
                parse_iso(candidate.published_at) or utcnow(),
            ),
            reverse=True,
        )

    def _select(
        self,
        candidates: list[RawCandidate],
        source_topics: list[str] | None = None,
        source_keywords: list[str] | None = None,
        limit: int | None = None,
    ) -> list[RawCandidate]:
        """Keep relevant candidates and a small exploration quota.

        When the user has stated goals or skills, an entire feed that matches
        none of them should not be dumped into the Inbox. One exploratory item
        per ten saved items keeps discovery possible without flooding the desk.
        Dedicated sources (topics) are always considered relevant.
        """
        limit = max(1, limit or self._limit())
        ranked = self._rank(candidates, source_topics, source_keywords)
        if (
            not self.profile.collection_terms()
            and not self.interests.top(1)
            and not source_topics
            and not source_keywords
        ):
            return ranked[:limit]
        relevant: list[RawCandidate] = []
        exploratory: list[RawCandidate] = []
        for candidate in ranked:
            if self._matches(candidate, source_topics, source_keywords):
                relevant.append(candidate)
            else:
                exploratory.append(candidate)
        exploration_cap = max(1, limit // 10)
        return (relevant[:limit] + exploratory[:exploration_cap])[:limit]

    def _related_notes(self, note_id: str, tags: list[str]) -> list[dict[str, Any]]:
        selected = [tag for tag in tags if tag][:8]
        if not selected:
            return []
        placeholders = ",".join("?" for _ in selected)
        with self.db.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT ni.note_id, ni.path, ni.title, COUNT(*) AS hits
                FROM note_tags nt
                JOIN note_index ni ON ni.note_id = nt.note_id
                WHERE nt.tag IN ({placeholders}) AND nt.note_id <> ? AND ni.kind <> 'review'
                GROUP BY ni.note_id
                ORDER BY hits DESC, ni.updated_at DESC
                LIMIT 3
                """,
                [*selected, note_id],
            ).fetchall()
        return [
            {
                "note_id": row["note_id"],
                "path": row["path"],
                "title": row["title"],
            }
            for row in rows
        ]

    def _why(self, matched: list[str]) -> str:
        if not matched:
            return "未匹配当前画像；作为来源更新或探索项收录，可在反馈中标记不相关。"
        goals = [
            item for item in matched
            if not item.startswith(("兴趣:", "来源主题:", "来源关键词:"))
        ]
        interests = [item.replace("兴趣:", "") for item in matched if item.startswith("兴趣:")]
        source_topics = [item.replace("来源主题:", "") for item in matched if item.startswith("来源主题:")]
        source_keywords = [item.replace("来源关键词:", "") for item in matched if item.startswith("来源关键词:")]
        parts = []
        if goals:
            parts.append("匹配技术栈/目标：" + "、".join(goals[:4]))
        if interests:
            parts.append("近期关注：" + "、".join(interests[:3]))
        if source_topics:
            parts.append("来源主题：" + "、".join(source_topics[:4]))
        if source_keywords:
            parts.append("命中来源关键词：" + "、".join(source_keywords[:4]))
        return "；".join(parts) + "。" if parts else "来自已订阅来源。"

    def _fallback_tags(self, candidate: RawCandidate, matched: list[str]) -> list[str]:
        tags = [normalize_tag(tag) for tag in candidate.tags]
        for term in matched:
            for prefix in ("兴趣:", "来源主题:", "来源关键词:"):
                if term.startswith(prefix):
                    term = term.split(":", 1)[1]
                    break
            tags.append(normalize_tag(term))
        if candidate.source_type != "paper":
            for token in re.findall(r"[A-Za-z][A-Za-z0-9.+#/_-]{2,}", candidate.title)[:8]:
                slug = normalize_tag(token)
                if slug in STOPWORDS or len(slug) < 3:
                    continue
                tags.append(slug)
        if candidate.source_type == "paper":
            tags.append("paper")
        if candidate.source_type == "repository":
            tags.append("github")
        return [tag for tag in unique(tags) if tag][:10]

    def _enrich(
        self, candidate: RawCandidate, matched: list[str]
    ) -> tuple[str, str, list[str], bool, str]:
        base_tags = self._fallback_tags(candidate, matched)
        source_text = candidate.content or candidate.summary
        language = _detect_language(source_text)
        fallback_title = candidate.title if language == "zh" else ""
        fallback_summary = first_sentences(source_text, 500)
        if not self.llm.available:
            return fallback_title, fallback_summary, base_tags, False, language
        try:
            result = self.llm.complete_json(
                "你是个人知识库编辑。把给定资讯翻译并概括为中文，只依据原文，不添加事实。"
                '输出 JSON：{"title_zh":"中文标题","summary":"2-4 句中文概括",'
                '"tags":["技术名或术语，最多 6 个"],"language":"zh|en|other"}。',
                f"原标题：{candidate.title}\n来源类型：{candidate.source_type}\n"
                f"原文：\n{source_text[:7000]}",
                max_tokens=1800,
                effort="low",
            )
            if result:
                title_zh = str(result.get("title_zh") or "").strip()[:300]
                summary = str(result.get("summary") or "").strip()
                tags = [normalize_tag(str(tag)) for tag in result.get("tags") or []]
                detected = str(result.get("language") or language).strip().lower()
                if detected not in {"zh", "en", "other"}:
                    detected = language
                if summary:
                    return (
                        title_zh or fallback_title,
                        summary[:1600],
                        unique([*tags, *base_tags])[:12],
                        True,
                        detected,
                    )
        except Exception:
            pass
        return fallback_title, fallback_summary, base_tags, False, language

    def _fallback_organize(
        self, candidate: RawCandidate, matched: list[str]
    ) -> tuple[str, str, list[str], bool, str]:
        source_text = candidate.content or candidate.summary
        language = _detect_language(source_text)
        title_zh = candidate.title if language == "zh" else ""
        return (
            title_zh,
            first_sentences(source_text, 500),
            self._fallback_tags(candidate, matched),
            False,
            language,
        )

    def _limit(self) -> int:
        return max(1, self.settings.get_int("collection_limit", 12))

    def list_items(self, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            if status:
                rows = conn.execute(
                    "SELECT i.*, s.name AS source_name, s.platform AS source_platform FROM items i "
                    "LEFT JOIN sources s ON s.id = i.source_id "
                    "WHERE i.status = ? ORDER BY i.fetched_at DESC LIMIT ?",
                    (status, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT i.*, s.name AS source_name, s.platform AS source_platform FROM items i "
                    "LEFT JOIN sources s ON s.id = i.source_id "
                    "ORDER BY i.fetched_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [_item_row(row, self.vault) for row in rows]

    def get_item(self, item_id: str) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT i.*, s.name AS source_name, s.platform AS source_platform FROM items i "
                "LEFT JOIN sources s ON s.id = i.source_id WHERE i.id = ?",
                (item_id,),
            ).fetchone()
            versions = conn.execute(
                "SELECT * FROM item_versions WHERE item_id = ? ORDER BY id DESC", (item_id,)
            ).fetchall()
        if row is None:
            return None
        result = _item_row(row, self.vault)
        result["content_text"] = row["content_text"] or row["excerpt"] or ""
        result["versions"] = [dict(version) for version in versions]
        return result

    def list_news(self, days: int = 7, limit: int = 100) -> list[dict[str, Any]]:
        cutoff = iso(utcnow() - timedelta(days=max(1, days)))
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT i.*, s.name AS source_name, s.platform AS source_platform FROM items i
                LEFT JOIN sources s ON s.id = i.source_id
                WHERE i.fetched_at >= ? AND i.status NOT IN ('not_relevant', 'skipped')
                ORDER BY i.fetched_at DESC LIMIT ?
                """,
                (cutoff, max(1, min(limit, 300))),
            ).fetchall()
        return [_item_row(row, self.vault) for row in rows]

    def feedback(self, item_id: str, status: str) -> dict[str, Any]:
        if status not in {"inbox", "read", "saved", "skipped", "not_relevant"}:
            raise ValueError("Unsupported item status")
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
        if row is None:
            raise KeyError(item_id)
        target_subdir = "10-Sources" if status == "saved" else "00-Inbox"
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE items SET status = ?, updated_at = ? WHERE id = ?",
                (status, iso(), item_id),
            )
        moved = None
        index = self.vault._row(item_id)
        if index is not None:
            if index["path"].split("/", 1)[0] != target_subdir:
                moved = self.vault.move_note(item_id, target_subdir)
            current = self.vault.read_note(item_id)
            if current is not None:
                frontmatter, body = _split_for_update(current["content"])
                frontmatter["status"] = status
                self.vault.write_note(
                    note_id=item_id,
                    kind="source",
                    title=current["title"],
                    body=body,
                    frontmatter=frontmatter,
                    subdir=target_subdir,
                    status=status,
                    tags=current["tags"],
                    on_conflict="skip",
                )
        if status in {"saved", "not_relevant"}:
            tags = json_loads(row["tags_json"], [])
            topics = [
                {"slug": tag, "label": self.tags.display_name(tag)}
                for tag in tags[:6]
            ]
            self.interests.record_interaction(
                "save" if status == "saved" else "not_relevant",
                row["title"],
                item_id=item_id,
                meta={"status": status},
                topics=topics,
            )
        index = self.vault._row(item_id)
        if index is not None:
            with self.db.connect() as conn:
                conn.execute(
                    "UPDATE items SET vault_path = ? WHERE id = ?", (index["path"], item_id)
                )
        return {"item": self.get_item(item_id), "move": moved}


def _source_row(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "kind": row["kind"],
        "name": row["name"],
        "platform": row["platform"] if "platform" in row.keys() else "",
        "config": json_loads(row["config_json"], {}),
        "enabled": bool(row["enabled"]),
        "interval_minutes": row["interval_minutes"],
        "last_run_at": row["last_run_at"],
        "last_status": row["last_status"],
        "last_error": row["last_error"],
    }


def _item_row(row: Any, vault: Vault) -> dict[str, Any]:
    note = vault.read_note(row["id"])
    keys = row.keys()
    return {
        "id": row["id"],
        "source_key": row["source_key"],
        "source_type": row["source_type"],
        "source_name": row["source_name"] if "source_name" in keys else "",
        "source_platform": row["source_platform"] if "source_platform" in keys else "",
        "canonical_url": row["canonical_url"],
        "title": row["title"],
        "title_zh": row["title_zh"] if "title_zh" in keys else "",
        "author": row["author"],
        "published_at": row["published_at"],
        "fetched_at": row["fetched_at"],
        "content_scope": row["content_scope"],
        "summary": row["summary"],
        "tags": json_loads(row["tags_json"], []),
        "matched": json_loads(row["matched_json"], []),
        "why": row["why"],
        "status": row["status"],
        "vault_path": row["vault_path"],
        "language": row["language"] if "language" in keys else "",
        "organization": row["organization"] if "organization" in keys else "fallback",
        "organized_at": row["organized_at"] if "organized_at" in keys else None,
        "note_id": row["id"],
        "app_url": f"/api/notes/{row['id']}",
        "obsidian_uri": note["obsidian_uri"] if note else None,
    }


def _split_for_update(content: str) -> tuple[dict[str, Any], str]:
    from .utils import split_frontmatter

    return split_frontmatter(content)


def _contains_term(haystack: str, term: str) -> bool:
    """Match Latin terms on word boundaries; Chinese terms by substring."""
    term = (term or "").strip().lower()
    if not term:
        return False
    if re.fullmatch(r"[\w+#./_-]+", term):
        return re.search(rf"(?<![\w]){re.escape(term)}(?![\w])", haystack) is not None
    return term in haystack


def _safe_external(value: str, limit: int, multiline: bool = False) -> str:
    """Neutralize HTML and Obsidian embeds from untrusted source text."""
    text = strip_tags(str(value or ""))
    if multiline:
        text = text.replace("\r\n", "\n").replace("\r", "\n")
    else:
        text = re.sub(r"\s+", " ", text)
    text = text.replace("[[", "[ [").replace("]]", "] ]")
    return text.strip()[:limit]


def _safe_url(value: str) -> str:
    url = str(value or "").strip()
    if not url.startswith(("http://", "https://")):
        return "#"
    return (
        url.replace(" ", "%20")
        .replace("(", "%28")
        .replace(")", "%29")
        .replace("<", "%3C")
        .replace(">", "%3E")
    )


def _wikilink_safe(value: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text.replace("|", "-").replace("[[", "[ [").replace("]]", "] ]")[:300]


def _wikilink_path(value: str) -> str:
    text = str(value or "").replace("\r", "").replace("\n", "")
    return text[:-3] if text.lower().endswith(".md") else text


def _detect_language(text: str) -> str:
    sample = (text or "")[:3000]
    cjk = sum(1 for ch in sample if "一" <= ch <= "鿿")
    letters = sum(1 for ch in sample if ch.isalpha())
    if cjk >= 10 and cjk / max(letters, 1) > 0.15:
        return "zh"
    if re.search(r"[A-Za-z]{3,}", sample):
        return "en"
    return "other"
