from __future__ import annotations

import pytest

from knowgraph.vault import VaultConflict


def test_atomic_write_and_manual_edit_creates_revision(app):
    written = app.vault.write_note(
        note_id="note_test1",
        kind="note",
        title="幂等性",
        body="系统生成的初稿。\n",
        tags=["distributed-systems"],
    )
    path = app.vault.safe_path(written["path"])
    assert path.exists()
    path.write_text(path.read_text(encoding="utf-8") + "\n人工补充。\n", encoding="utf-8")

    second = app.vault.write_note(
        note_id="note_test1",
        kind="note",
        title="幂等性",
        body="系统生成的新版本。\n",
        tags=["distributed-systems"],
    )
    assert second["conflict"] is True
    assert second["note_id"] != "note_test1"
    assert "人工补充" in path.read_text(encoding="utf-8")
    revision = app.vault.safe_path(second["path"]).read_text(encoding="utf-8")
    assert "系统生成的新版本" in revision
    assert "supersedes: note_test1" in revision


def test_unedited_note_is_safely_replaced(app):
    first = app.vault.write_note(note_id="note_test2", kind="note", title="A", body="v1")
    second = app.vault.write_note(note_id="note_test2", kind="note", title="A", body="v2")
    assert second["conflict"] is False
    assert app.vault.read_note("note_test2")["body"].strip() == "v2"
    assert first["path"] == second["path"]


def test_path_escape_is_rejected(app):
    with pytest.raises(VaultConflict):
        app.vault.safe_path("../outside.md")


def test_sync_indexes_external_obsidian_note(app, tmp_path):
    external = app.paths.vault / "20-Notes" / "external.md"
    external.write_text(
        "---\nid: note_external\ntitle: 外部笔记\ntags: [Java, 并发]\n---\n\n"
        "Java 虚拟线程与并发控制。\n",
        encoding="utf-8",
    )
    result = app.vault.sync_from_disk()
    assert result["indexed"] >= 1
    note = app.vault.read_note("note_external")
    assert note is not None
    assert "虚拟线程" in note["body"]
    hits = app.search.search("虚拟线程")["results"]
    assert any(hit["note_id"] == "note_external" for hit in hits)
