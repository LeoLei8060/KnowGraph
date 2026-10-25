"""Weekly and monthly reviews built from local evidence, not from model memory."""

from __future__ import annotations

import calendar
import json
import re
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .config import RuntimeSettings
from .db import Database, json_dumps, json_loads
from .interests import InterestService
from .llm import LLMRefusal, LLMService, LLMUnavailable
from .utils import iso, parse_iso, stable_id
from .vault import Vault


def period_bounds(
    period_type: str,
    anchor: date,
    tz: ZoneInfo,
    offset: int = 0,
) -> tuple[datetime, datetime, str, str]:
    if period_type == "week":
        monday = anchor - timedelta(days=anchor.weekday()) + timedelta(weeks=offset)
        start_date = monday
        end_date = monday + timedelta(days=7)
        label = f"{monday.isocalendar().year}-W{monday.isocalendar().week:02d}"
    elif period_type == "month":
        month_index = anchor.year * 12 + (anchor.month - 1) + offset
        year, month_zero = divmod(month_index, 12)
        month = month_zero + 1
        start_date = date(year, month, 1)
        days = calendar.monthrange(year, month)[1]
        end_date = start_date + timedelta(days=days)
        label = f"{year}-{month:02d}"
    else:
        raise ValueError("period_type must be week or month")
    start = datetime.combine(start_date, time.min, tzinfo=tz).astimezone(timezone.utc)
    end = datetime.combine(end_date, time.min, tzinfo=tz).astimezone(timezone.utc)
    return start, end, label, period_type


class ReviewService:
    def __init__(
        self,
        db: Database,
        settings: RuntimeSettings,
        vault: Vault,
        llm: LLMService,
        interests: InterestService,
    ) -> None:
        self.db = db
        self.settings = settings
        self.vault = vault
        self.llm = llm
        self.interests = interests

    def preview(self, period_type: str, offset: int = -1) -> dict[str, Any]:
        tz = self.settings.timezone()
        anchor = datetime.now(tz).date()
        start, end, label, _ = period_bounds(period_type, anchor, tz, offset)
        return {
            "period_type": period_type,
            "label": label,
            "start": iso(start),
            "end": iso(end),
            "stats": self._stats(start, end),
        }

    def generate(self, period_type: str, offset: int = -1, force: bool = False) -> dict[str, Any]:
        tz = self.settings.timezone()
        anchor = datetime.now(tz).date()
        start, end, label, _ = period_bounds(period_type, anchor, tz, offset)
        if not force:
            existing = self._find(period_type, start, end)
            if existing:
                return self.get(existing["id"])
        stats, evidence = self._collect(start, end)
        summary = self._summary(period_type, label, start, end, stats, evidence)
        note_id = stable_id("review", f"{period_type}:{start.date().isoformat()}")
        body = self._render_body(period_type, label, start, end, stats, evidence, summary)
        written = self.vault.write_note(
            note_id=note_id,
            kind="review",
            title=f"{label} {'周回顾' if period_type == 'week' else '月度回顾'}",
            body=body,
            frontmatter={
                "period_type": period_type,
                "period_start": iso(start),
                "period_end": iso(end),
                "label": label,
            },
            subdir="50-Reviews",
            status="draft",
            tags=["review", period_type],
        )
        with self.db.connect() as conn:
            max_revision = conn.execute(
                "SELECT MAX(revision) AS r FROM reviews WHERE period_type = ? AND period_start = ? AND period_end = ?",
                (period_type, iso(start), iso(end)),
            ).fetchone()["r"]
            revision = int(max_revision or 0) + 1
            review_id = stable_id("rev", f"{period_type}:{iso(start)}:{revision}:{written['note_id']}")
            conn.execute(
                """
                INSERT INTO reviews(id, period_type, period_start, period_end, revision, status,
                                    summary_md, stats_json, vault_path, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'draft', ?, ?, ?, ?, ?)
                """,
                (
                    review_id,
                    period_type,
                    iso(start),
                    iso(end),
                    revision,
                    summary,
                    json_dumps(stats),
                    written["path"],
                    iso(),
                    iso(),
                ),
            )
            for item in evidence:
                conn.execute(
                    "INSERT INTO review_evidence(review_id, kind, ref_id, title, detail) VALUES (?, ?, ?, ?, ?)",
                    (review_id, item["kind"], item.get("ref_id"), item.get("title", ""), item.get("detail", "")),
                )
        return self.get(review_id)

    def _find(self, period_type: str, start: datetime, end: datetime) -> Any:
        with self.db.connect() as conn:
            return conn.execute(
                "SELECT * FROM reviews WHERE period_type = ? AND period_start = ? AND period_end = ? "
                "ORDER BY revision DESC LIMIT 1",
                (period_type, iso(start), iso(end)),
            ).fetchone()

    def list(self, limit: int = 30) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM reviews ORDER BY period_start DESC, revision DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._row(row, include_body=False) for row in rows]

    def get(self, review_id: str) -> dict[str, Any]:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM reviews WHERE id = ?", (review_id,)).fetchone()
            evidence = conn.execute(
                "SELECT * FROM review_evidence WHERE review_id = ? ORDER BY id", (review_id,)
            ).fetchall()
        if row is None:
            raise KeyError(review_id)
        result = self._row(row, include_body=True)
        result["evidence"] = [dict(item) for item in evidence]
        return result

    def _row(self, row: Any, include_body: bool) -> dict[str, Any]:
        tz = self.settings.timezone()
        start = parse_iso(row["period_start"])
        end = parse_iso(row["period_end"])
        local_start = start.astimezone(tz) if start else None
        local_end = (end - timedelta(seconds=1)).astimezone(tz) if end else None
        if local_start:
            if row["period_type"] == "week":
                iso_cal = local_start.isocalendar()
                label = f"{iso_cal.year}-W{iso_cal.week:02d}"
            else:
                label = f"{local_start.year}-{local_start.month:02d}"
        else:
            label = ""
        result = {
            "id": row["id"],
            "period_type": row["period_type"],
            "period_start": row["period_start"],
            "period_end": row["period_end"],
            "period_start_local": local_start.date().isoformat() if local_start else None,
            "period_end_local": local_end.date().isoformat() if local_end else None,
            "label": label,
            "revision": row["revision"],
            "status": row["status"],
            "summary": row["summary_md"],
            "stats": json_loads(row["stats_json"], {}),
            "vault_path": row["vault_path"],
            "created_at": row["created_at"],
        }
        if include_body:
            note = None
            with self.db.connect() as conn:
                note_row = conn.execute(
                    "SELECT note_id FROM note_index WHERE path = ?", (row["vault_path"],)
                ).fetchone()
            if note_row:
                note = self.vault.read_note(note_row["note_id"])
            result["note_id"] = note_row["note_id"] if note_row else None
            result["body"] = note["body"] if note else ""
            result["obsidian_uri"] = note["obsidian_uri"] if note else None
        return result

    def _stats(self, start: datetime, end: datetime) -> dict[str, Any]:
        start_s, end_s = iso(start), iso(end)
        with self.db.connect() as conn:
            items = conn.execute(
                "SELECT status, COUNT(*) AS n FROM items WHERE fetched_at >= ? AND fetched_at < ? GROUP BY status",
                (start_s, end_s),
            ).fetchall()
            total_items = conn.execute(
                "SELECT COUNT(*) AS n FROM items WHERE fetched_at >= ? AND fetched_at < ?",
                (start_s, end_s),
            ).fetchone()["n"]
            interactions = conn.execute(
                "SELECT kind, COUNT(*) AS n FROM interactions WHERE created_at >= ? AND created_at < ? GROUP BY kind",
                (start_s, end_s),
            ).fetchall()
            top_queries = conn.execute(
                "SELECT query, COUNT(*) AS n FROM interactions WHERE created_at >= ? AND created_at < ? "
                "AND kind IN ('search','ask') AND query <> '' GROUP BY query ORDER BY n DESC LIMIT 8",
                (start_s, end_s),
            ).fetchall()
            progress = conn.execute(
                "SELECT * FROM progress_logs WHERE occurred_at >= ? AND occurred_at < ? ORDER BY occurred_at DESC",
                (start_s, end_s),
            ).fetchall()
            goals = conn.execute(
                "SELECT ref_id, detail_json, created_at FROM audit_log WHERE kind = 'goal_updated' "
                "AND created_at >= ? AND created_at < ? ORDER BY created_at DESC",
                (start_s, end_s),
            ).fetchall()
            inbox_pending = conn.execute("SELECT COUNT(*) AS n FROM items WHERE status IN ('inbox','read')").fetchone()["n"]
            notes_updated = conn.execute(
                "SELECT COUNT(*) AS n FROM note_index WHERE updated_at >= ? AND updated_at < ?",
                (start_s, end_s),
            ).fetchone()["n"]
        status_counts = {row["status"]: row["n"] for row in items}
        interaction_counts = {row["kind"]: row["n"] for row in interactions}
        progress_minutes = sum(int(row["minutes"] or 0) for row in progress)
        return {
            "items_total": total_items,
            "items_by_status": status_counts,
            "inbox_pending": inbox_pending,
            "notes_updated": notes_updated,
            "interactions": interaction_counts,
            "searches": interaction_counts.get("search", 0),
            "questions": interaction_counts.get("ask", 0),
            "top_queries": [dict(row) for row in top_queries],
            "progress_count": len(progress),
            "progress_minutes": progress_minutes,
            "progress": [dict(row) for row in progress],
            "goal_changes": [
                {
                    "ref_id": row["ref_id"],
                    "change": json_loads(row["detail_json"], {}),
                    "created_at": row["created_at"],
                }
                for row in goals
            ],
        }

    def _collect(self, start: datetime, end: datetime) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        stats = self._stats(start, end)
        evidence: list[dict[str, Any]] = []
        start_s, end_s = iso(start), iso(end)
        with self.db.connect() as conn:
            items = conn.execute(
                "SELECT id, title, vault_path, status FROM items WHERE fetched_at >= ? AND fetched_at < ? "
                "ORDER BY fetched_at DESC LIMIT 20",
                (start_s, end_s),
            ).fetchall()
        for row in items:
            evidence.append(
                {
                    "kind": "item",
                    "ref_id": row["id"],
                    "title": row["title"],
                    "detail": row["vault_path"] or "",
                }
            )
        for goal in stats["goal_changes"]:
            evidence.append(
                {
                    "kind": "goal",
                    "ref_id": goal["ref_id"],
                    "title": "目标状态更新",
                    "detail": goal["created_at"],
                }
            )
        for progress in stats["progress"]:
            evidence.append(
                {
                    "kind": "progress",
                    "ref_id": progress["id"],
                    "title": progress["title"],
                    "detail": f"{progress['status']} · {progress['minutes']} 分钟",
                }
            )
        return stats, evidence

    def _summary(
        self,
        period_type: str,
        label: str,
        start: datetime,
        end: datetime,
        stats: dict[str, Any],
        evidence: list[dict[str, Any]],
    ) -> str:
        deterministic = _deterministic_summary(period_type, label, stats)
        if not self.llm.available:
            return deterministic
        try:
            parsed = self.llm.complete_json(
                "你负责写个人知识库的周/月回顾。只能使用给定统计和证据，不得编造学习成果。"
                "搜索、提问、阅读只代表关注，不等于掌握。没有学习记录时必须明确说明。"
                "输出 JSON：{\"summary\":\"3-5 句\",\"suggestions\":[\"最多 3 条具体建议\"]}。",
                json.dumps(
                    {
                        "period_type": period_type,
                        "label": label,
                        "start": iso(start),
                        "end": iso(end),
                        "stats": stats,
                        "evidence_titles": [item["title"] for item in evidence[:20]],
                    },
                    ensure_ascii=False,
                ),
                max_tokens=1200,
                effort="medium",
            )
            if parsed and parsed.get("summary"):
                suggestions = parsed.get("suggestions") or []
                text = str(parsed["summary"]).strip()
                if suggestions:
                    text += "\n\n**模型建议（需人工判断）**\n" + "\n".join(
                        f"- {item}" for item in suggestions[:3]
                    )
                return text
        except (LLMUnavailable, LLMRefusal, Exception):
            pass
        return deterministic

    def _render_body(
        self,
        period_type: str,
        label: str,
        start: datetime,
        end: datetime,
        stats: dict[str, Any],
        evidence: list[dict[str, Any]],
        summary: str,
    ) -> str:
        tz = self.settings.timezone()
        start_local = start.astimezone(tz).strftime("%Y-%m-%d")
        end_local = (end.astimezone(tz) - timedelta(seconds=1)).strftime("%Y-%m-%d")
        lines = [
            f"# {label} {'周回顾' if period_type == 'week' else '月度回顾'}",
            "",
            f"> 数据范围：{start_local} 至 {end_local}（{tz.key}）。本回顾只引用本地资料和状态事件。",
            "",
            "## 概览",
            f"- 新增资料：{stats['items_total']} 条",
            f"- 已保存：{stats['items_by_status'].get('saved', 0)} 条；待处理 Inbox：{stats['inbox_pending']} 条",
            f"- 检索：{stats['searches']} 次；提问：{stats['questions']} 次",
            f"- 学习记录：{stats['progress_count']} 条，共 {stats['progress_minutes']} 分钟",
            "",
            "## 关注主题",
        ]
        if stats["top_queries"]:
            for row in stats["top_queries"]:
                lines.append(f"- {_wikilink_safe(row['query'])}（{row['n']} 次）")
        else:
            lines.append("- 本周期没有主动检索或问答记录。")
        lines.extend(["", "## 资料与证据"])
        if evidence:
            for item in evidence[:20]:
                path = item.get("detail") or ""
                if item["kind"] == "item" and path:
                    lines.append(f"- [[{_wikilink_path(path)}|{_wikilink_safe(item['title'])}]]")
                elif item["kind"] == "progress":
                    lines.append(f"- 学习记录：{_wikilink_safe(item['title'])}（{_wikilink_safe(item['detail'])}）")
                elif item["kind"] == "goal":
                    lines.append(f"- 目标状态更新：{_wikilink_safe(item['title'])}（{_wikilink_safe(item['detail'])}）")
        else:
            lines.append("- 本周期没有新增资料或状态事件。")
        lines.extend(["", "## 学习与目标状态"])
        if stats["progress"]:
            for row in stats["progress"]:
                lines.append(
                    f"- {row['occurred_at'][:10]} · {_wikilink_safe(row['title'])} · {_wikilink_safe(row['status'])}"
                    + (f" · {_wikilink_safe(row['skill'])}" if row["skill"] else "")
                    + f" · {row['minutes']} 分钟"
                )
        else:
            lines.append("- 尚无学习完成记录。检索和阅读只能说明关注，不能说明技能已掌握。")
        if stats["goal_changes"]:
            lines.append("")
            lines.append("目标状态变更：")
            for change in stats["goal_changes"]:
                lines.append(f"- {change['created_at'][:10]} · {change['ref_id']}")
        lines.extend(["", "## 回顾与下期建议", _wikilink_safe(summary) or "本周期暂无足够数据生成建议。"])
        return "\n".join(lines).strip() + "\n"

    def attach_summary(self, review: dict[str, Any]) -> dict[str, Any]:
        review["summary"] = review.get("summary") or ""
        return review


def _wikilink_safe(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text.replace("|", "-").replace("[[", "[ [").replace("]]", "] ]")[:500]


def _wikilink_path(value: Any) -> str:
    text = str(value or "").replace("\r", "").replace("\n", "")
    return text[:-3] if text.lower().endswith(".md") else text


def _deterministic_summary(period_type: str, label: str, stats: dict[str, Any]) -> str:
    lines = [
        f"{label} 共新增 {stats['items_total']} 条资料，完成 {stats['searches']} 次检索、"
        f"{stats['questions']} 次提问。"
    ]
    if stats["progress_count"]:
        lines.append(
            f"记录了 {stats['progress_count']} 条学习事项，累计 {stats['progress_minutes']} 分钟；"
            "这些记录可作为技能进展的证据。"
        )
    else:
        lines.append(
            "本周期没有学习完成记录。检索和阅读只说明关注，不能据此判断技能已经提升。"
        )
    if stats["top_queries"]:
        topics = "、".join(row["query"] for row in stats["top_queries"][:3])
        lines.append(f"主动关注集中在：{topics}。")
    if stats["inbox_pending"]:
        lines.append(f"当前 Inbox 仍有 {stats['inbox_pending']} 条资料待处理。")
    suggestions = []
    if stats["progress_count"] == 0:
        suggestions.append("为一个短期目标补充一次可验证的练习或学习记录。")
    if stats["inbox_pending"] > 10:
        suggestions.append("先处理 Inbox 中与当前目标最相关的资料，避免只收藏不消化。")
    if stats["top_queries"]:
        suggestions.append("检查高频检索主题是否已形成可复用的笔记或练习。")
    if not suggestions:
        suggestions.append("保持当前节奏，并在下期回顾中比较完成率和重复出现的主题。")
    lines.append("下期建议：" + " ".join(suggestions[:3]))
    return "\n\n".join(lines)
