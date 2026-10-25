from __future__ import annotations

from datetime import timedelta

from knowgraph.utils import iso, utcnow


def test_interests_follow_queries_but_never_profile(app, candidate):
    profile_before = app.profile.get()
    app.profile.add_skill("Java", level=3)
    app.profile.add_goal("long", "成为后端架构师")
    stable_before = app.profile.get()
    for _ in range(3):
        app.interests.record_interaction("search", "Redis Lua 原子性")
    labels = {item["slug"] for item in app.interests.list()}
    assert "redis" in labels
    stable_after = app.profile.get()
    assert stable_after["skills"] == stable_before["skills"]
    assert stable_after["goals"] == stable_before["goals"]
    assert profile_before["name"] == stable_after["name"]


def test_blocked_interest_is_excluded_and_delete_clears_evidence(app):
    for _ in range(2):
        app.interests.record_interaction("ask", "Kubernetes 调度器")
    assert any(item["slug"] == "kubernetes" for item in app.interests.top())
    app.interests.set_blocked("kubernetes", True)
    assert not any(item["slug"] == "kubernetes" for item in app.interests.top())
    app.interests.delete_interest("kubernetes")
    assert not any(item["slug"] == "kubernetes" for item in app.interests.list())


def test_interest_decays_with_age(app):
    app.interests.record_interaction("search", "Rust 所有权")
    fresh = next(item for item in app.interests.list() if item["slug"] == "rust")["score"]
    with app.db.connect() as conn:
        old = iso(utcnow() - timedelta(days=28))
        conn.execute("UPDATE interactions SET created_at = ?", (old,))
    app.interests.recompute()
    aged_rows = [item for item in app.interests.list() if item["slug"] == "rust"]
    assert not aged_rows or aged_rows[0]["score"] < fresh


def test_tracking_can_be_disabled(app):
    app.settings.set("track_interactions", "0")
    app.interests.record_interaction("search", "Go 泛型")
    assert app.interests.list() == []


def test_saving_item_uses_tags_as_interest_topics(app, candidate):
    app.ingest.ingest(candidate, enrich=False)
    item_id = app.ingest.list_items()[0]["id"]
    app.ingest.feedback(item_id, "saved")
    slugs = {item["slug"] for item in app.interests.list()}
    assert "redis" in slugs and "c++" in slugs
    assert not any(len(slug) > 8 for slug in slugs)


def test_profile_links_are_saved(app):
    app.profile.update({"name": "Ada", "links": {"github": "https://github.com/ada"}})
    profile = app.profile.get()
    assert profile["links"]["github"] == "https://github.com/ada"
    assert profile["name"] == "Ada"


def test_blocked_topic_survives_history_clear_and_pinned_topic_drives_top(app):
    app.interests.set_blocked("rust", True)
    app.interests.record_interaction("search", "Rust 所有权")
    app.interests.clear_history()
    blocked = [item for item in app.interests.list() if item["slug"] == "rust"]
    assert blocked and blocked[0]["blocked"] is True

    app.interests.set_pinned("message-queue", True)
    assert any(item["slug"] == "message-queue" for item in app.interests.top(10))
