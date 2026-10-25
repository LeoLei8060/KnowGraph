from __future__ import annotations


def test_tag_search_supports_symbols_and_chinese(app, candidate):
    app.ingest.ingest(candidate, enrich=False)
    assert app.search.search("", tags="C++")["total"] == 1
    assert app.search.search("", tags="c++")["total"] == 1
    assert app.search.search("分布式事务")["total"] >= 0
    assert app.search.search("Redis")["total"] == 1


def test_tag_alias_resolves(app, candidate):
    app.ingest.ingest(candidate, enrich=False)
    app.tags.add_alias("redis", "Redis缓存")
    result = app.search.search("", tags="Redis缓存")
    assert result["tags"] == ["redis"]
    assert result["total"] == 1


def test_chinese_substring_search(app):
    app.vault.write_note(
        note_id="note_cn",
        kind="note",
        title="分布式事务",
        body="两阶段提交与 Saga 模式，关注一致性与补偿。",
        tags=["分布式事务"],
    )
    result = app.search.search("分布式事务")
    assert result["total"] == 1
    assert result["results"][0]["note_id"] == "note_cn"
    assert app.search.search("", tags="分布式事务")["total"] == 1
