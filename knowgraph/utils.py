"""Shared small utilities: time, IDs, slugs, hashing, HTML and frontmatter."""

from __future__ import annotations

import hashlib
import html
import re
import unicodedata
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable

import yaml


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None = None) -> str:
    dt = dt or utcnow()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def stable_id(prefix: str, key: str, length: int = 12) -> str:
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:length]
    return f"{prefix}_{digest}"


_INVALID_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def filename_slug(text: str, limit: int = 80) -> str:
    text = unicodedata.normalize("NFKC", text or "").strip()
    text = _INVALID_FILENAME.sub("-", text)
    text = re.sub(r"\s+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip(" .-")
    return (text[:limit].strip(" .-") or "untitled")


def tag_slug(text: str) -> str:
    """Canonical tag key. Keeps language syntax such as C++ and async/await."""
    text = unicodedata.normalize("NFKC", text or "").strip().lower()
    text = text.lstrip("#")
    text = re.sub(r"\s+", "-", text)
    text = re.sub(r"-{2,}", "-", text)
    text = re.sub(r"[^\w㐀-鿿+#./_-]", "", text)
    return text.strip("-")[:80]


def split_frontmatter(markdown: str) -> tuple[dict[str, Any], str]:
    if not markdown.startswith("---"):
        return {}, markdown
    parts = markdown.split("---", 2)
    if len(parts) < 3:
        return {}, markdown
    try:
        data = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        return {}, markdown
    if not isinstance(data, dict):
        return {}, markdown
    return data, parts[2].lstrip("\n")


def render_markdown(frontmatter: dict[str, Any], body: str) -> str:
    yaml_text = yaml.safe_dump(
        frontmatter,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=120,
    ).strip()
    return f"---\n{yaml_text}\n---\n\n{body.strip()}\n"


class _TextExtractor(HTMLParser):
    """Small, dependency-free readable-text extractor.

    Deliberately conservative: it keeps visible prose and drops navigation,
    scripts and styling. It is not a full readability implementation.
    """

    SKIP = {"script", "style", "noscript", "nav", "footer", "header", "form", "svg"}
    BLOCK = {"p", "h1", "h2", "h3", "h4", "li", "blockquote", "pre", "td", "th", "div"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.skip_depth = 0
        self.parts: list[str] = []
        self.title = ""
        self.meta: dict[str, str] = {}
        self._in_title = False
        self._current_tag: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in self.SKIP:
            self.skip_depth += 1
        if tag == "title":
            self._in_title = True
        if tag == "meta":
            attr = {k.lower(): (v or "") for k, v in attrs}
            name = (attr.get("name") or attr.get("property") or "").lower()
            if name in {"description", "og:description", "og:title", "author"}:
                self.meta[name] = attr.get("content", "").strip()
        if tag == "link":
            attr = {k.lower(): (v or "") for k, v in attrs}
            if attr.get("rel", "").lower() == "canonical":
                self.meta["canonical"] = attr.get("href", "").strip()
        if tag in self.BLOCK:
            self._current_tag = tag
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self.SKIP and self.skip_depth:
            self.skip_depth -= 1
        if tag == "title":
            self._in_title = False
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
            return
        if self.skip_depth:
            return
        text = data.strip()
        if text:
            self.parts.append(text + " ")

    def text(self) -> str:
        joined = "".join(self.parts)
        joined = re.sub(r"[ \t]+", " ", joined)
        joined = re.sub(r"\n\s*\n+", "\n\n", joined)
        return joined.strip()


def extract_html(raw: str, fallback_title: str = "") -> tuple[str, str, dict[str, str]]:
    parser = _TextExtractor()
    parser.feed(raw)
    parser.close()
    title = (parser.title or parser.meta.get("og:title") or fallback_title).strip()
    return title, parser.text(), parser.meta


def strip_tags(raw: str) -> str:
    if not raw:
        return ""
    return html.unescape(re.sub(r"<[^>]+>", " ", raw)).strip()


def first_sentences(text: str, limit: int = 420) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    for mark in ("。", ". ", "！", "？", "\n"):
        idx = cut.rfind(mark)
        if idx >= limit * 0.55:
            return cut[: idx + len(mark)].strip()
    return cut.rstrip() + "…"


def paragraph_chunks(text: str, max_chars: int = 900) -> list[str]:
    raw = [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]
    chunks: list[str] = []
    for paragraph in raw:
        if len(paragraph) <= max_chars:
            chunks.append(paragraph)
            continue
        for start in range(0, len(paragraph), max_chars):
            chunks.append(paragraph[start : start + max_chars])
    return chunks


def unique(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            output.append(item)
    return output


def relative_to(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()
