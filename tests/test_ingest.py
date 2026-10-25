from __future__ import annotations

from knowgraph.adapters import RawCandidate


def test_ingest_dedupes_and_versions(app, candidate):
    first = app.ingest.ingest(candidate, enrich=False)
    second = app.ingest.ingest(candidate, enrich=False)
    assert first["created"] is True
    assert second["created"] is False and second["updated"] is False
    assert len(app.ingest.list_items()) == 1

    changed = type(candidate)(
        **{**candidate.__dict__, "content": candidate.content + "\n新增一段内容。"}
    )
    third = app.ingest.ingest(changed, enrich=False)
    assert third["updated"] is True
    item = app.ingest.get_item(first["id"])
    assert len(item["versions"]) == 1


def test_saved_item_moves_to_sources(app, candidate):
    item_id = app.ingest.ingest(candidate, enrich=False)["id"]
    before = app.ingest.get_item(item_id)
    assert before["vault_path"].startswith("00-Inbox/")
    result = app.ingest.feedback(item_id, "saved")
    assert result["item"]["status"] == "saved"
    assert result["item"]["vault_path"].startswith("10-Sources/")


def test_manual_edit_is_not_overwritten_by_feedback(app, candidate):
    item_id = app.ingest.ingest(candidate, enrich=False)["id"]
    item = app.ingest.get_item(item_id)
    path = app.vault.safe_path(item["vault_path"])
    path.write_text(path.read_text(encoding="utf-8") + "\n我的批注\n", encoding="utf-8")
    app.ingest.feedback(item_id, "saved")
    assert "我的批注" in path.read_text(encoding="utf-8")


def test_sync_does_not_revert_db_status_of_manually_edited_card(app, candidate):
    item_id = app.ingest.ingest(candidate, enrich=False)["id"]
    item = app.ingest.get_item(item_id)
    path = app.vault.safe_path(item["vault_path"])
    path.write_text(path.read_text(encoding="utf-8") + "\n我的批注\n", encoding="utf-8")
    app.ingest.feedback(item_id, "saved")
    app.vault.sync_from_disk()
    assert app.ingest.get_item(item_id)["status"] == "saved"


def test_unrelated_feed_is_not_dumped_into_inbox(app, candidate, monkeypatch):
    app.profile.add_skill("Redis", level=2)
    source = app.sources.add("arxiv", "arXiv test", {"categories": ["cs.AI"]})
    unrelated = [
        RawCandidate(
            source_key=f"arxiv:unrelated-{index}",
            source_type="paper",
            title=f"Unrelated language model paper {index}",
            url=f"https://arxiv.org/abs/unrelated-{index}",
            summary="A paper about a topic the user has not declared.",
            content="Nothing about the user's goals.",
        )
        for index in range(8)
    ]
    monkeypatch.setattr("knowgraph.ingest.fetch_source", lambda source, context: [candidate, *unrelated])
    result = app.ingest.collect_source(source)
    assert result["saved"] == 2
    titles = [item["title"] for item in app.ingest.list_items()]
    assert any("Redis" in title for title in titles)
    assert len(titles) == 2


def test_interest_matching_uses_word_boundaries(app):
    app.interests.record_interaction("search", "Lua 脚本")
    unrelated = RawCandidate(
        source_key="arxiv:evaluation",
        source_type="paper",
        title="Evaluating model outputs",
        url="https://arxiv.org/abs/evaluation",
        summary="Evaluation methods for language models.",
        content="Evaluation and benchmarking.",
    )
    assert app.ingest._matches(unrelated) == []
    related = RawCandidate(
        source_key="demo:lua",
        source_type="article",
        title="Lua scripts in Redis",
        url="https://example.org/lua",
        summary="Lua scripting.",
        content="Lua runs on the server.",
    )
    assert any(match.startswith("兴趣:") for match in app.ingest._matches(related))


def test_paper_tags_drop_english_stopwords(app, monkeypatch):
    source = app.sources.add("arxiv", "arXiv tags", {"categories": ["cs.AI"]})
    paper = RawCandidate(
        source_key="arxiv:tag-test",
        source_type="paper",
        title="Optimization for Thought",
        url="https://arxiv.org/abs/tag-test",
        summary="A paper.",
        content="A paper.",
        tags=["cs.AI", "arxiv"],
    )
    monkeypatch.setattr("knowgraph.ingest.fetch_source", lambda source, context: [paper])
    app.ingest.collect_source(source)
    item = app.ingest.list_items()[0]
    assert "for" not in item["tags"]
    assert "paper" in item["tags"]


def test_external_wikilinks_are_neutralized_in_cards(app):
    malicious = RawCandidate(
        source_key="demo:malicious",
        source_type="article",
        title="Title [[embed]]",
        url="https://example.org/malicious",
        summary="See ![[private-note]] and <script>alert(1)</script>",
        content="Body ![[private-attachment]]",
        content_scope="excerpt",
        tags=["test"],
    )
    item_id = app.ingest.ingest(malicious, enrich=False)["id"]
    card = app.vault.read_note(item_id)["content"]
    assert "![[private" not in card
    assert "[ [private" in card
    assert "<script>" not in card


def test_item_card_links_related_notes_by_tag(app, candidate):
    app.vault.write_note(
        note_id="note_related",
        kind="note",
        title="Redis 事务笔记",
        body="已有的人工笔记。",
        tags=["Redis"],
    )
    item_id = app.ingest.ingest(candidate, enrich=False)["id"]
    card = app.vault.read_note(item_id)["content"]
    assert "## 相关笔记" in card
    assert "20-Notes/Redis-事务笔记--note-related" in card
    assert "related_ids:" in card
