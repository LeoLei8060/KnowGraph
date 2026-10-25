from __future__ import annotations


class FakeExtractionLLM:
    available = True

    def __init__(self, payload):
        self.payload = payload
        self.prompt = ""

    def complete_json(self, system, prompt, **kwargs):
        self.prompt = prompt
        return self.payload


def test_model_extraction_filters_and_normalizes(app):
    fake = FakeExtractionLLM(
        {
            "skills": [
                {"name": "C++11/14/17/20", "category": "language", "level": 3, "evidence": "C++11/14/17/20"},
                {"name": "Qt 5/6", "category": "framework", "level": 3, "evidence": "Qt 5/6"},
                {"name": "QML", "category": "framework", "level": 2, "evidence": "QML"},
                {"name": "团队管理", "category": "other", "level": 3, "evidence": "团队管理"},
                {"name": "PMP", "category": "other", "level": 3, "evidence": "PMP"},
                {"name": "完全不存在技术", "category": "language", "level": 2, "evidence": "完全不存在技术"},
            ]
        }
    )
    app.profile.llm = fake
    text = """## 核心技能
| 语言与框架 | C++11/14/17/20、Qt 5/6、QML |
| 团队与协作 | 团队管理、PMP 认证 |
"""
    suggestions = app.profile.suggest_from_resume(text)
    names = {item["name"] for item in suggestions}
    assert "C++" in names
    assert "Qt" in names
    assert "QML" in names
    assert "团队管理" not in names
    assert "PMP" not in names
    assert "完全不存在技术" not in names
    assert any("核心技能" in line for line in fake.prompt.splitlines())
    by_name = {item["name"]: item for item in suggestions}
    assert by_name["C++"]["category"] == "language"
    assert by_name["Qt"]["category"] == "framework"


def test_save_resume_replaces_pending_suggestions_and_accepts_category(app):
    app.profile.llm = FakeExtractionLLM(
        {"skills": [{"name": "SQLite", "category": "database", "level": 3, "evidence": "SQLite"}]}
    )
    first = app.profile.suggest_from_resume("## 核心技能\nSQLite")
    assert first
    app.profile.llm = FakeExtractionLLM(
        {"skills": [{"name": "Qt", "category": "framework", "level": 3, "evidence": "Qt"}]}
    )
    result = app.profile.save_resume("## 核心技能\nQt", "resume.md")
    pending = result["profile"]["suggestions"]
    assert {item["name"] for item in pending} == {"Qt"}
    accepted = app.profile.accept_suggestion(pending[0]["id"])
    assert accepted["name"] == "Qt"
    assert accepted["category"] == "framework"
    assert app.profile.get()["suggestions"] == []


def test_accept_all_and_clear_suggestions(app):
    app.profile.llm = FakeExtractionLLM(
        {
            "skills": [
                {"name": "SQLite", "category": "database", "level": 3, "evidence": "SQLite"},
                {"name": "C++", "category": "language", "level": 3, "evidence": "C++"},
            ]
        }
    )
    app.profile.suggest_from_resume("## 核心技能\nC++、SQLite")
    result = app.profile.accept_all_suggestions()
    assert result["accepted"] == 2
    skills = {item["name"]: item["category"] for item in result["profile"]["skills"]}
    assert skills["SQLite"] == "database"
    assert skills["C++"] == "language"
    assert app.profile.get()["suggestions"] == []

    app.profile.llm = FakeExtractionLLM(
        {"skills": [{"name": "Qt", "category": "framework", "level": 3, "evidence": "Qt"}]}
    )
    app.profile.suggest_from_resume("## 核心技能\nQt")
    app.profile.clear_suggestions()
    assert app.profile.get()["suggestions"] == []
