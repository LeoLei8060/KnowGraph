from __future__ import annotations


class FakeLLM:
    available = True

    def __init__(self, payload):
        self.payload = payload

    def complete_json(self, *args, **kwargs):
        return self.payload


def test_local_answer_has_valid_clickable_citation(app, candidate):
    app.ingest.ingest(candidate, enrich=False)
    result = app.qa.ask("Redis 如何保证事务原子性？", mode="local")
    assert result["mode"] in {"extractive", "grounded"}
    assert result["citations"]
    for citation in result["citations"]:
        note = app.vault.read_note(citation["note_id"])
        assert note is not None
        assert citation["path"] == note["path"]
        assert citation["app_url"] == f"/api/notes/{citation['note_id']}"


def test_no_local_docs_is_explicit(app):
    result = app.qa.ask("完全不存在的问题 xyzzy", mode="local")
    assert result["mode"] == "insufficient"
    assert result["citations"] == []
    assert "未找到" in result["answer"]


def test_invalid_model_citation_is_dropped(app, candidate):
    app.ingest.ingest(candidate, enrich=False)
    app.qa.llm = FakeLLM(
        {"answer": "Redis 使用 Lua。", "citations": [{"id": "C99", "quote": "不存在"}]}
    )
    result = app.qa.ask("Redis 事务", mode="local")
    assert result["mode"] == "insufficient"
    assert result["citations"] == []


def test_valid_model_citation_quote_is_checked(app, candidate):
    app.ingest.ingest(candidate, enrich=False)
    evidence = app.search.evidence("Redis 事务", limit=1)
    quote = evidence[0]["snippet"][:20]
    app.qa.llm = FakeLLM(
        {"answer": "Lua 在服务端原子执行。", "citations": [{"id": "C1", "quote": quote}]}
    )
    result = app.qa.ask("Redis 事务", mode="auto")
    assert result["mode"] == "grounded"
    assert result["citations"][0]["note_id"]


def test_natural_language_question_retrieves_local_note(app):
    app.vault.write_note(
        note_id="note_idempotency",
        kind="note",
        title="幂等键",
        body="幂等键在一次请求重试时保持相同，服务端据此判断该操作已经执行，从而避免重复扣款。",
        tags=["幂等", "支付"],
    )
    result = app.qa.ask("幂等键如何避免重复扣款？", mode="local")
    assert result["citations"]
    assert result["citations"][0]["note_id"] == "note_idempotency"
