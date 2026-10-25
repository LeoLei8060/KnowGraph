from __future__ import annotations


def test_qa_history_groups_and_filters(app, candidate):
    app.ingest.ingest(candidate, enrich=False)
    group = app.history.create_group("Redis 专题")
    result = app.qa.ask("Redis 事务", mode="local", group_id=group["id"])
    assert result["history_id"]

    groups = app.history.groups()
    assert groups["total"] == 1
    assert groups["groups"][0]["count"] == 1
    entries = app.history.list(group_id=group["id"])
    assert len(entries) == 1
    assert entries[0]["group_name"] == "Redis 专题"

    other = app.history.create_group("并发")
    app.history.assign(result["history_id"], other["id"])
    assert app.history.list(group_id=group["id"]) == []
    assert len(app.history.list(group_id=other["id"])) == 1

    app.history.delete_group(other["id"])
    assert len(app.history.list(ungrouped=True)) == 1


def test_qa_history_stale_citation_is_marked(app, candidate):
    item_id = app.ingest.ingest(candidate, enrich=False)["id"]
    app.qa.ask("Redis 事务", mode="local")
    entry = app.history.list()[0]
    detail = app.history.get(entry["id"])
    assert detail["citations"][0]["stale"] is False
    app.vault.remove_note_file(item_id)
    detail = app.history.get(entry["id"])
    assert detail["citations"][0]["stale"] is True
    assert detail["citations"][0]["obsidian_uri"] is None


def test_qa_history_can_be_disabled(app, candidate):
    app.ingest.ingest(candidate, enrich=False)
    app.settings.set("qa_history_enabled", "0")
    result = app.qa.ask("Redis 事务", mode="local")
    assert result["history_id"] is None
    assert app.history.list() == []


def test_qa_history_api_group_flow(client, candidate, app):
    app.ingest.ingest(candidate, enrich=False)
    group = client.post("/api/qa/groups", json={"name": "事务"}).json()
    answer = client.post(
        "/api/ask",
        json={"question": "Redis 事务", "mode": "local", "group_id": group["id"]},
    ).json()
    assert answer["history_id"]
    entries = client.get(f"/api/qa/history?group_id={group['id']}").json()["entries"]
    assert len(entries) == 1
    detail = client.get(f"/api/qa/history/{entries[0]['id']}").json()
    assert detail["group_name"] == "事务"
    renamed = client.patch(f"/api/qa/groups/{group['id']}", json={"name": "事务与一致性"})
    assert renamed.status_code == 200
    cleared = client.delete(f"/api/qa/groups/{group['id']}")
    assert cleared.status_code == 200
    assert client.get("/api/qa/history?ungrouped=true").json()["entries"]
