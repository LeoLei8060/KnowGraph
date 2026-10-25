"""Hybrid local search: canonical tags, full text and deterministic ranking."""

from __future__ import annotations

import re
from typing import Any, Iterable

from .db import Database, json_loads
from .tags import TagService, extract_topics, normalize_tag
from .utils import paragraph_chunks, unique

TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9.+#/_-]{1,}|[㐀-鿿]{1,24}")
CJK_RE = re.compile(r"[㐀-鿿]")


class SearchService:
    def __init__(self, db: Database, tags: TagService | None = None) -> None:
        self.db = db
        self.tags = tags

    def search(
        self,
        query: str = "",
        tags: Iterable[str] | str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        query = (query or "").strip()
        tag_list = self._tag_list(tags)
        query_terms = _query_terms(query, self.tags)
        scores: dict[str, float] = {}
        matched: dict[str, set[str]] = {}
        snippets: dict[str, str] = {}
        fts_rank: dict[str, float] = {}
        notes: dict[str, Any] = {}

        def add(note_id: str, score: float, reason: str) -> None:
            scores[note_id] = scores.get(note_id, 0.0) + score
            matched.setdefault(note_id, set()).add(reason)

        with self.db.connect() as conn:
            if tag_list:
                placeholders = ",".join("?" for _ in tag_list)
                rows = conn.execute(
                    f"SELECT note_id, COUNT(*) AS hits FROM note_tags WHERE tag IN ({placeholders}) "
                    "GROUP BY note_id",
                    tag_list,
                ).fetchall()
                for row in rows:
                    add(row["note_id"], 10.0 + 2.0 * (row["hits"] - 1), "tag")

            if query:
                for term in query_terms:
                    for token, prefix in _fts_tokens(term):
                        fts_query = f'"{token}"' + ("*" if prefix else "")
                        try:
                            rows = conn.execute(
                                "SELECT note_id, bm25(note_fts) AS rank, "
                                "snippet(note_fts, 2, '⟦', '⟧', ' … ', 24) AS snip "
                                "FROM note_fts WHERE note_fts MATCH ? ORDER BY rank LIMIT ?",
                                (fts_query, max(limit * 3, 30)),
                            ).fetchall()
                        except Exception:
                            rows = []
                        for index, row in enumerate(rows):
                            add(row["note_id"], max(2.0, 8.0 - index * 0.15), "fulltext")
                            fts_rank[row["note_id"]] = float(row["rank"] or 0)
                            if row["snip"]:
                                snippets.setdefault(row["note_id"], _clean_snippet(row["snip"]))

                like_terms = unique([*query_terms, query])
                for term in like_terms:
                    like = f"%{term}%"
                    rows = conn.execute(
                        "SELECT note_id, title, body FROM note_index "
                        "WHERE title LIKE ? OR body LIKE ? OR tags_json LIKE ? LIMIT ?",
                        (like, like, like, max(limit * 3, 30)),
                    ).fetchall()
                    for row in rows:
                        score = 5.0 if term.lower() in row["title"].lower() else 3.0
                        add(row["note_id"], score, "substring")
                        snippets.setdefault(row["note_id"], _snippet(row["body"], term))

            if not query and not tag_list:
                rows = conn.execute(
                    "SELECT note_id FROM note_index ORDER BY updated_at DESC LIMIT ?", (limit,)
                ).fetchall()
                for row in rows:
                    add(row["note_id"], 1.0, "recent")

            ids = list(scores.keys())
            if ids:
                placeholders = ",".join("?" for _ in ids)
                for row in conn.execute(
                    f"SELECT * FROM note_index WHERE note_id IN ({placeholders})", ids
                ).fetchall():
                    notes[row["note_id"]] = row

        results: list[dict[str, Any]] = []
        for note_id, score in scores.items():
            row = notes.get(note_id)
            if row is None:
                continue
            body = row["body"] or ""
            results.append(
                {
                    "note_id": note_id,
                    "title": row["title"],
                    "path": row["path"],
                    "kind": row["kind"],
                    "status": row["status"],
                    "tags": json_loads(row["tags_json"], []),
                    "updated_at": row["updated_at"],
                    "score": round(score, 3),
                    "matched_by": sorted(matched.get(note_id, [])),
                    "snippet": snippets.get(note_id) or _snippet(body, query_terms[0] if query_terms else query, length=260),
                }
            )
        results.sort(key=lambda item: (-item["score"], fts_rank.get(item["note_id"], 0), item["title"]))
        return {"query": query, "tags": tag_list, "total": len(results), "results": results[:limit]}

    def evidence(self, query: str, tags: Iterable[str] | str | None = None, limit: int = 8) -> list[dict[str, Any]]:
        """Return note-level evidence paragraphs for grounded answers."""
        query_terms = _query_terms(query, self.tags)
        hits = self.search(query, tags=tags, limit=max(limit * 2, 12))["results"]
        evidence: list[dict[str, Any]] = []
        for hit in hits:
            with self.db.connect() as conn:
                row = conn.execute(
                    "SELECT body FROM note_index WHERE note_id = ?", (hit["note_id"],)
                ).fetchone()
            body = row["body"] if row else ""
            paragraphs = paragraph_chunks(body)
            if not paragraphs:
                paragraphs = [hit["title"]]
            ranked = sorted(
                ((_paragraph_score(p, query_terms), p) for p in paragraphs),
                key=lambda item: item[0],
                reverse=True,
            )
            for _, paragraph in ranked[:2]:
                if not paragraph:
                    continue
                evidence.append(
                    {
                        "note_id": hit["note_id"],
                        "title": hit["title"],
                        "path": hit["path"],
                        "kind": hit["kind"],
                        "snippet": paragraph[:900],
                        "score": hit["score"],
                    }
                )
                if len(evidence) >= limit:
                    return evidence
        return evidence


    def _tag_list(self, tags: Iterable[str] | str | None) -> list[str]:
        if not tags:
            return []
        if isinstance(tags, str):
            raw = re.split(r"[,，;；\s]+", tags)
        else:
            raw = list(tags)
        output: list[str] = []
        for tag in raw:
            slug = (
                self.tags.resolve(str(tag))
                if self.tags is not None
                else normalize_tag(str(tag))
            )
            if slug and slug not in output:
                output.append(slug)
        return output


def _fts_tokens(query: str) -> list[tuple[str, bool]]:
    tokens: list[tuple[str, bool]] = []
    for match in TOKEN_RE.findall(query):
        token = match.replace('"', '""')
        if not token:
            continue
        # CJK runs benefit from prefix matching because FTS5's unicode61 keeps a
        # whole Chinese run as one token.
        prefix = bool(CJK_RE.match(token)) and len(token) >= 2
        tokens.append((token, prefix))
    return tokens[:8]


def _clean_snippet(value: str) -> str:
    return re.sub(r"\s+", " ", value).replace("⟦", "[").replace("⟧", "]").strip()


def _snippet(body: str, query: str, length: int = 320) -> str:
    body = re.sub(r"\s+", " ", body or "")
    if not query:
        return body[:length]
    index = body.lower().find(query.lower())
    if index < 0:
        terms = TOKEN_RE.findall(query)
        for term in terms:
            index = body.lower().find(term.lower())
            if index >= 0:
                break
    if index < 0:
        return body[:length]
    start = max(0, index - length // 3)
    end = min(len(body), start + length)
    prefix = "…" if start else ""
    suffix = "…" if end < len(body) else ""
    return f"{prefix}{body[start:end]}{suffix}"


def _paragraph_score(paragraph: str, terms: list[str]) -> float:
    lowered = paragraph.lower()
    score = 0.0
    for term in terms:
        term = term.lower()
        if not term:
            continue
        if term in lowered:
            score += 2.0 if len(term) > 1 else 0.5
        elif len(term) > 2 and CJK_RE.match(term):
            bigrams = {term[i : i + 2] for i in range(len(term) - 1)}
            score += 0.4 * sum(1 for gram in bigrams if gram in lowered)
    return score


def _query_terms(query: str, tags: TagService | None = None) -> list[str]:
    """Expand a natural-language question into local search terms.

    FTS5's unicode61 tokenizer treats a whole Chinese sentence as one token, so
    searching the raw question would miss notes containing only its key phrase.
    """
    query = (query or "").strip()
    if not query:
        return []
    aliases = tags.aliases() if tags is not None else {}
    topics = extract_topics(query, aliases, limit=10)
    terms = [topic["label"].strip() for topic in topics if topic.get("label", "").strip()]
    if not terms:
        terms = [token for token in TOKEN_RE.findall(query) if len(token) > 1] or [query]
    return unique([term for term in terms if term])[:10]
