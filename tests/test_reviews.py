from __future__ import annotations

from datetime import date
from zoneinfo import ZoneInfo

from knowgraph.reviews import period_bounds


def test_week_period_is_calendar_week():
    tz = ZoneInfo("Asia/Shanghai")
    start, end, label, _ = period_bounds("week", date(2026, 10, 25), tz, 0)
    assert start.astimezone(tz).date().isoformat() == "2026-10-19"
    assert end.astimezone(tz).date().isoformat() == "2026-10-26"
    assert label == "2026-W43"


def test_month_period_handles_year_boundary():
    tz = ZoneInfo("Asia/Shanghai")
    start, end, label, _ = period_bounds("month", date(2026, 1, 15), tz, -1)
    assert start.astimezone(tz).date().isoformat() == "2025-12-01"
    assert end.astimezone(tz).date().isoformat() == "2026-01-01"
    assert label == "2025-12"


def test_review_does_not_overwrite_manual_edit(app):
    first = app.reviews.generate("week", offset=-1)
    path = app.vault.safe_path(first["vault_path"])
    path.write_text(path.read_text(encoding="utf-8") + "\n手工补充的回顾。\n", encoding="utf-8")
    second = app.reviews.generate("week", offset=-1, force=True)
    assert second["id"] != first["id"]
    assert "手工补充的回顾" in path.read_text(encoding="utf-8")
    assert second["vault_path"] != first["vault_path"]
    revision = app.vault.safe_path(second["vault_path"]).read_text(encoding="utf-8")
    assert "supersedes" in revision


def test_review_without_learning_records_does_not_claim_mastery(app):
    review = app.reviews.generate("week", offset=-1, force=True)
    body = review["body"]
    assert "没有学习完成记录" in body or "尚无学习完成记录" in body
    assert "已经掌握" not in body


def test_progress_is_evidence_in_review(app):
    app.progress.add("完成 Redis 幂等练习", skill="Redis", status="done", minutes=45)
    review = app.reviews.generate("week", offset=0, force=True)
    assert review["stats"]["progress_minutes"] == 45
    assert "完成 Redis 幂等练习" in review["body"]
