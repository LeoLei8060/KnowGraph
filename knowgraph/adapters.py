"""Source adapters. Every adapter returns normalized candidates; none writes to the vault."""

from __future__ import annotations

import ipaddress
import os
import re
import socket
import urllib.robotparser
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import httpx

from .utils import extract_html, iso, parse_iso, strip_tags

USER_AGENT = "KnowGraph/0.1 (+local personal knowledge manager)"
ATOM = "{http://www.w3.org/2005/Atom}"
TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "ref", "ref_src", "source", "spm", "from", "fbclid", "gclid",
}


class SourceError(RuntimeError):
    pass


@dataclass
class RawCandidate:
    source_key: str
    source_type: str
    title: str
    url: str
    summary: str = ""
    content: str = ""
    author: str = ""
    published_at: str | None = None
    content_scope: str = "metadata"
    tags: list[str] = field(default_factory=list)
    raw_meta: dict[str, Any] = field(default_factory=dict)


def canonicalize_url(url: str, base: str | None = None) -> str:
    if base:
        url = urljoin(base, url)
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower() or "https"
    netloc = parts.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k.lower() not in TRACKING_PARAMS]
    path = re.sub(r"/{2,}", "/", parts.path).rstrip("/") or "/"
    return urlunsplit((scheme, netloc, path, urlencode(query), ""))


def _is_private_host(host: str) -> bool:
    host = host.strip("[]").lower()
    if host in {"localhost", "0.0.0.0"} or host.endswith((".local", ".internal")):
        return True
    try:
        address = ipaddress.ip_address(host)
        return address.is_private or address.is_loopback or address.is_link_local or address.is_reserved
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return True
    for info in infos:
        try:
            address = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved:
            return True
    return False


def ensure_public_url(url: str, allow_private: bool = False) -> str:
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise SourceError(f"Unsupported URL: {url}")
    if not allow_private and _is_private_host(parts.hostname or ""):
        raise SourceError("Private or loopback URLs are blocked. Enable allow_private_urls to override.")
    return url


_ROBOTS: dict[str, urllib.robotparser.RobotFileParser] = {}


def robots_allowed(url: str) -> bool:
    parts = urlsplit(url)
    origin = f"{parts.scheme}://{parts.netloc}"
    parser = _ROBOTS.get(origin)
    if parser is None:
        parser = urllib.robotparser.RobotFileParser()
        parser.set_url(origin + "/robots.txt")
        try:
            parser.read()
        except Exception:
            _ROBOTS[origin] = parser
            return True
        _ROBOTS[origin] = parser
    try:
        return parser.can_fetch(USER_AGENT, url)
    except Exception:
        return True


def _client(trust_env: bool = True) -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"},
        timeout=httpx.Timeout(20.0, connect=10.0),
        follow_redirects=True,
        trust_env=trust_env,
    )


def _get(url: str, *, max_bytes: int = 2_000_000, trust_env: bool = True) -> tuple[str, str]:
    with _client(trust_env) as client:
        with client.stream("GET", url) as response:
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > max_bytes:
                    break
                chunks.append(chunk)
            data = b"".join(chunks)
    try:
        return data.decode(response.encoding or "utf-8", errors="replace"), content_type
    except Exception:
        return data.decode("utf-8", errors="replace"), content_type


def fetch_article_text(
    url: str,
    *,
    allow_private: bool = False,
    trust_env: bool = True,
    max_chars: int = 50_000,
) -> tuple[str, str, dict[str, str]]:
    """Fetch and extract readable article text for a selected item."""
    url = ensure_public_url(url, allow_private=allow_private)
    if not robots_allowed(url):
        raise SourceError(f"robots.txt disallows fetching {url}")
    body, content_type = _get(url, max_bytes=3_000_000, trust_env=trust_env)
    if "html" not in content_type and not body.lstrip().startswith("<"):
        return url.rsplit("/", 1)[-1] or url, body[:max_chars], {}
    title, text, meta = extract_html(body, fallback_title=url)
    return title, text[:max_chars], meta


def _parse_date(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    try:
        parsed = parse_iso(value)
        if parsed:
            return iso(parsed)
    except Exception:
        pass
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return iso(datetime.strptime(value, fmt))
        except ValueError:
            continue
    return None


class RSSAdapter:
    def fetch(self, config: dict[str, Any], context: dict[str, Any]) -> list[RawCandidate]:
        url = ensure_public_url(
            str(config.get("url") or "").strip(),
            allow_private=bool(context.get("allow_private_urls")),
        )
        if not robots_allowed(url):
            raise SourceError(f"robots.txt disallows fetching {url}")
        raw, _ = _get(
            url,
            max_bytes=int(config.get("max_bytes") or 8_000_000),
            trust_env=not config.get("no_proxy"),
        )
        try:
            root = ET.fromstring(raw.encode("utf-8"))
        except ET.ParseError as exc:
            raise SourceError(f"Invalid RSS/Atom feed: {exc}") from exc
        candidates: list[RawCandidate] = []
        if root.tag == f"{ATOM}feed":
            entries = root.findall(f"{ATOM}entry")
            for entry in entries:
                link = ""
                for node in entry.findall(f"{ATOM}link"):
                    if node.get("rel") in (None, "alternate"):
                        link = node.get("href") or ""
                        break
                title = _text(entry.find(f"{ATOM}title"))
                summary = strip_tags(_text(entry.find(f"{ATOM}summary")))
                published = _parse_date(_text(entry.find(f"{ATOM}published")) or _text(entry.find(f"{ATOM}updated")))
                author = _text(entry.find(f"{ATOM}author/{ATOM}name"))
                if link and title:
                    canonical = canonicalize_url(link)
                    candidates.append(
                        RawCandidate(
                            source_key=f"url:{canonical}",
                            source_type="article",
                            title=title,
                            url=canonical,
                            summary=summary[:1200],
                            content=summary,
                            author=author,
                            published_at=published,
                            content_scope="excerpt",
                            raw_meta={"feed": url, "entry_id": _text(entry.find(f"{ATOM}id"))},
                        )
                    )
        else:
            channel = root.find("channel") or root
            for item in channel.findall("item"):
                link = _text(item.find("link"))
                title = _text(item.find("title"))
                summary = strip_tags(_text(item.find("description")))
                published = _parse_date(_text(item.find("pubDate")))
                author = _text(item.find("author"))
                if link and title:
                    canonical = canonicalize_url(link)
                    candidates.append(
                        RawCandidate(
                            source_key=f"url:{canonical}",
                            source_type="article",
                            title=title,
                            url=canonical,
                            summary=summary[:1200],
                            content=summary,
                            author=author,
                            published_at=published,
                            content_scope="excerpt",
                            raw_meta={"feed": url},
                        )
                    )
        return candidates[: int(context.get("per_source_limit") or 30)]


class WebAdapter:
    def fetch(self, config: dict[str, Any], context: dict[str, Any]) -> list[RawCandidate]:
        urls = config.get("urls") or ([config["url"]] if config.get("url") else [])
        results: list[RawCandidate] = []
        for raw_url in urls[:10]:
            url = ensure_public_url(
                str(raw_url).strip(), allow_private=bool(context.get("allow_private_urls"))
            )
            if not robots_allowed(url):
                raise SourceError(f"robots.txt disallows fetching {url}")
            body, content_type = _get(url, trust_env=not config.get("no_proxy"))
            if "html" not in content_type and not body.lstrip().startswith("<"):
                text = body.strip()
                results.append(
                    RawCandidate(
                        source_key=f"url:{canonicalize_url(url)}",
                        source_type="document",
                        title=url.rsplit("/", 1)[-1] or url,
                        url=canonicalize_url(url),
                        summary=text[:500],
                        content=text[:20000],
                        content_scope="full",
                    )
                )
                continue
            title, text, meta = extract_html(body, fallback_title=url)
            canonical = canonicalize_url(meta.get("canonical") or url, base=url)
            summary = meta.get("description") or meta.get("og:description") or text[:500]
            results.append(
                RawCandidate(
                    source_key=f"url:{canonical}",
                    source_type="article",
                    title=title or canonical,
                    url=canonical,
                    summary=summary[:1200],
                    content=text[:30000],
                    author=meta.get("author", ""),
                    content_scope="full" if len(text) >= 500 else "excerpt",
                    raw_meta={"canonical": canonical},
                )
            )
        return results


class GitHubAdapter:
    def fetch(self, config: dict[str, Any], context: dict[str, Any]) -> list[RawCandidate]:
        repos = config.get("repos") or []
        if isinstance(repos, str):
            repos = [part.strip() for part in repos.split(",") if part.strip()]
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        token = os.getenv("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        output: list[RawCandidate] = []
        errors: list[str] = []
        with httpx.Client(headers={**headers, "User-Agent": USER_AGENT}, timeout=20.0) as client:
            for repo in repos[:20]:
                full_name = str(repo).strip()
                try:
                    response = client.get(f"https://api.github.com/repos/{full_name}")
                    response.raise_for_status()
                    data = response.json()
                except Exception as exc:
                    errors.append(f"{full_name}: {exc}")
                    continue
                release = None
                try:
                    rel = client.get(f"https://api.github.com/repos/{full_name}/releases/latest")
                    if rel.status_code == 200:
                        release = rel.json()
                except Exception:
                    release = None
                topics = data.get("topics") or []
                description = data.get("description") or ""
                summary = description
                if release and release.get("body"):
                    summary = f"{description}\n\n{strip_tags(release['body'])[:1200]}"
                output.append(
                    RawCandidate(
                        source_key=f"github:{data.get('full_name') or full_name}",
                        source_type="repository",
                        title=data.get("full_name") or full_name,
                        url=data.get("html_url") or f"https://github.com/{full_name}",
                        summary=summary.strip()[:1800],
                        content=summary,
                        author=(data.get("owner") or {}).get("login", ""),
                        published_at=_parse_date((release or {}).get("published_at") or data.get("pushed_at")),
                        content_scope="metadata",
                        tags=[data.get("language") or "", *topics],
                        raw_meta={
                            "stars": data.get("stargazers_count"),
                            "forks": data.get("forks_count"),
                            "language": data.get("language"),
                            "topics": topics,
                            "release": (release or {}).get("tag_name"),
                            "pushed_at": data.get("pushed_at"),
                        },
                    )
                )
        if not output and errors:
            raise SourceError("; ".join(errors[:3]))
        return output


class ArxivAdapter:
    def fetch(self, config: dict[str, Any], context: dict[str, Any]) -> list[RawCandidate]:
        categories = config.get("categories") or []
        if isinstance(categories, str):
            categories = [part.strip() for part in categories.split(",") if part.strip()]
        query = str(config.get("query") or "").strip()
        terms = [f"cat:{cat}" for cat in categories[:6]]
        if query:
            terms.append(f'all:"{query}"')
        search_query = " OR ".join(terms) if terms else "cat:cs.AI"
        limit = min(int(context.get("per_source_limit") or 20), 40)
        api = (
            "https://export.arxiv.org/api/query?"
            + urlencode(
                {
                    "search_query": search_query,
                    "sortBy": "submittedDate",
                    "sortOrder": "descending",
                    "max_results": limit,
                }
            )
        )
        raw, _ = _get(api)
        root = ET.fromstring(raw.encode("utf-8"))
        output: list[RawCandidate] = []
        for entry in root.findall(f"{ATOM}entry"):
            title = _text(entry.find(f"{ATOM}title"))
            summary = _text(entry.find(f"{ATOM}summary"))
            link = _text(entry.find(f"{ATOM}id"))
            published = _parse_date(_text(entry.find(f"{ATOM}published")))
            authors = [
                _text(node.find(f"{ATOM}name"))
                for node in entry.findall(f"{ATOM}author")
            ]
            categories_found = [node.get("term", "") for node in entry.findall(f"{ATOM}category")]
            if not title or not link:
                continue
            arxiv_id = link.rsplit("/", 1)[-1]
            canonical_id = re.sub(r"v\d+$", "", arxiv_id)
            output.append(
                RawCandidate(
                    source_key=f"arxiv:{canonical_id}",
                    source_type="paper",
                    title=re.sub(r"\s+", " ", title).strip(),
                    url=link.replace("http://", "https://"),
                    summary=re.sub(r"\s+", " ", summary).strip()[:1600],
                    content=re.sub(r"\s+", " ", summary).strip(),
                    author=", ".join([a for a in authors if a][:8]),
                    published_at=published,
                    content_scope="excerpt",
                    tags=[*categories_found, "arxiv"],
                    raw_meta={"arxiv_id": canonical_id, "authors": authors[:8]},
                )
            )
        return output


def _text(node: ET.Element | None) -> str:
    if node is None:
        return ""
    return "".join(node.itertext()).strip()


ADAPTERS = {
    "rss": RSSAdapter(),
    "web": WebAdapter(),
    "github": GitHubAdapter(),
    "arxiv": ArxivAdapter(),
}


def fetch_source(source: dict[str, Any], context: dict[str, Any]) -> list[RawCandidate]:
    kind = source["kind"]
    adapter = ADAPTERS.get(kind)
    if adapter is None:
        raise SourceError(f"Unsupported source kind: {kind}")
    config = source.get("config") or {}
    return adapter.fetch(config, context)
