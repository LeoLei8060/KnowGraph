"""Tag normalization, aliases and lightweight topic extraction for interests."""

from __future__ import annotations

import re
from typing import Iterable
from .db import Database, json_dumps, json_loads
from .utils import iso, stable_id, tag_slug
from .utils import unique

STOPWORDS = {
    "the", "and", "for", "with", "from", "that", "this", "what", "how", "why", "when",
    "where", "which", "who", "are", "was", "were", "you", "your", "about", "into",
    "can", "could", "should", "would", "does", "did", "not", "but", "use", "using",
    "best", "good", "learn", "learning", "tutorial", "guide", "please", "explain",
    "difference", "between", "vs", "code", "example",
    "如何", "怎么", "怎样", "什么", "为什么", "哪些", "是否", "可以", "请问", "帮我",
    "解释", "介绍", "一下", "关于", "以及", "还有", "这个", "那个", "学习", "了解",
    "教程", "资料", "知识", "问题", "想要", "希望", "需要",
}

TOPIC_PREFIXES = (
    "我想学习", "我想了解", "帮我解释", "请解释", "什么是", "如何理解", "怎么理解",
    "如何实现", "怎么实现", "如何", "怎么", "为什么", "请问", "介绍一下", "解释一下",
)


def normalize_tag(value: str) -> str:
    return tag_slug(value)


def clean_topic(value: str) -> str:
    value = value.strip(" \t\r\n,，。.！!？?、；;:：()（）[]【】\"'“”")
    for prefix in TOPIC_PREFIXES:
        if value.startswith(prefix) and len(value) > len(prefix):
            value = value[len(prefix) :]
            break
    for suffix in ("是什么", "有哪些", "怎么用", "如何使用", "的用法"):
        if value.endswith(suffix) and len(value) > len(suffix):
            value = value[: -len(suffix)]
            break
    return value.strip()


LATIN_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9.+#/_-]{1,}")
CJK_RUN = re.compile(r"[㐀-鿿]{2,24}")


def extract_topics(text: str, known_aliases: dict[str, str] | None = None, limit: int = 5) -> list[dict[str, str]]:
    """Return candidate topics with a slug and display label.

    This is intentionally conservative and deterministic; it is not a semantic
    model. Unknown English technology terms are kept, common question words are
    dropped, and the number of topics is capped.
    """
    text = clean_topic(text or "")
    known_aliases = known_aliases or {}
    found: list[tuple[str, str]] = []
    lowered = text.lower()
    for alias, slug in sorted(known_aliases.items(), key=lambda item: len(item[0]), reverse=True):
        if not alias:
            continue
        if re.fullmatch(r"[\w+#./_-]+", alias):
            matched = re.search(rf"(?<![\w]){re.escape(alias)}(?![\w])", lowered)
        else:
            matched = alias in lowered
        if matched:
            found.append((slug, alias))

    for token in LATIN_TOKEN.findall(text):
        slug = normalize_tag(token)
        if not slug or slug in STOPWORDS or len(slug) < 2:
            continue
        if slug.isdigit():
            continue
        found.append((slug, token))

    for run in CJK_RUN.findall(text):
        run = clean_topic(run)
        if len(run) < 2 or run in STOPWORDS:
            continue
        if re.fullmatch(r"[㐀-鿿]+", run):
            # Split a long question into meaningful fragments around common particles.
            fragments = re.split(
                r"(?:如何|怎么|怎样|为什么|哪些|的|了|吗|呢|和|与|或|在|是|有|用|做|写|看|学)",
                run,
            )
            candidates = [frag for frag in fragments if 2 <= len(frag) <= 12] or [run[:12]]
        else:
            candidates = [run]
        for fragment in candidates:
            clean = clean_topic(fragment)
            if clean and clean not in STOPWORDS:
                found.append((normalize_tag(clean), clean))

    output: list[dict[str, str]] = []
    seen: set[str] = set()
    cjk_added = 0
    for slug, label in found:
        if not slug or slug in seen:
            continue
        if re.fullmatch(r"[㐀-鿿]+", slug):
            if not (2 <= len(slug) <= 6) or cjk_added >= 2:
                continue
            cjk_added += 1
        seen.add(slug)
        output.append({"slug": slug, "label": label.strip()[:80]})
        if len(output) >= limit:
            break
    return output


class TagService:
    def __init__(self, db: Database) -> None:
        self.db = db

    def list(self) -> list[dict]:
        with self.db.connect() as conn:
            rows = conn.execute("SELECT * FROM tags ORDER BY name COLLATE NOCASE").fetchall()
        return [
            {
                "id": row["id"],
                "name": row["name"],
                "slug": row["slug"],
                "aliases": json_loads(row["aliases_json"], []),
                "parent_slug": row["parent_slug"],
            }
            for row in rows
        ]

    def aliases(self) -> dict[str, str]:
        mapping: dict[str, str] = {}
        for tag in self.list():
            mapping[tag["slug"]] = tag["slug"]
            for alias in [tag["name"], *tag["aliases"]]:
                key = str(alias).strip().lower()
                if key:
                    mapping[key] = tag["slug"]
        return mapping

    def register(self, name: str, aliases: Iterable[str] = ()) -> dict:
        slug = normalize_tag(name)
        if not slug:
            raise ValueError("Empty tag")
        if isinstance(aliases, str):
            aliases = re.split(r"[,，;；]", aliases)
        aliases_clean = unique([normalize_tag(a) for a in aliases if normalize_tag(a)])
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM tags WHERE slug = ?", (slug,)).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO tags(id, name, slug, aliases_json, created_at) VALUES (?, ?, ?, ?, ?)",
                    (stable_id("tag", slug), name.strip()[:80], slug, json_dumps(aliases_clean), iso()),
                )
                row = conn.execute("SELECT * FROM tags WHERE slug = ?", (slug,)).fetchone()
            else:
                existing = json_loads(row["aliases_json"], [])
                merged = unique([*existing, *aliases_clean])
                conn.execute(
                    "UPDATE tags SET aliases_json = ? WHERE slug = ?", (json_dumps(merged), slug)
                )
        return {
            "id": row["id"],
            "name": row["name"],
            "slug": row["slug"],
            "aliases": json_loads(row["aliases_json"], []),
        }

    def add_alias(self, slug: str, alias: str) -> dict:
        alias_slug = normalize_tag(alias)
        if not alias_slug:
            raise ValueError("Empty alias")
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM tags WHERE slug = ?", (slug,)).fetchone()
            if row is None:
                raise KeyError(slug)
            aliases = unique([*json_loads(row["aliases_json"], []), alias_slug])
            conn.execute(
                "UPDATE tags SET aliases_json = ? WHERE slug = ?", (json_dumps(aliases), slug)
            )
        return {"slug": slug, "aliases": aliases}

    def resolve(self, raw: str) -> str:
        value = normalize_tag(raw)
        aliases = self.aliases()
        return aliases.get(value, aliases.get(str(raw).strip().lower(), value))

    def display_name(self, slug: str) -> str:
        with self.db.connect() as conn:
            row = conn.execute("SELECT name FROM tags WHERE slug = ?", (slug,)).fetchone()
        return row["name"] if row else slug
