"""User-owned facts: resume, skills and goals. Never inferred from search behaviour."""

from __future__ import annotations

import re
from typing import Any

from .db import Database, json_dumps, json_loads
from .llm import LLMService
from .tags import normalize_tag
from .utils import iso, stable_id, unique

TECH_TERMS = [
    "Python", "Java", "Go", "Rust", "C++", "C#", "TypeScript", "JavaScript", "Kotlin",
    "Swift", "SQL", "Shell", "React", "Vue", "Angular", "Spring Boot", "Spring",
    "Django", "Flask", "FastAPI", "Node.js", "Kubernetes", "Docker", "Redis",
    "MySQL", "PostgreSQL", "MongoDB", "Kafka", "RabbitMQ", "Elasticsearch",
    "AWS", "GCP", "Azure", "Linux", "Windows", "Git", "Machine Learning",
    "Deep Learning", "PyTorch", "TensorFlow", "LLM", "RAG", "Data Engineering",
    "Distributed Systems", "Microservices", "System Design", "Observability",
    "CI/CD", "Terraform", "Qt", "QML", "STL", "OCCT", "CEF", "OpenGL",
    "QPainter", "QSS", "SQLite", "CMake", "QMake", "Visual Studio", "VS Code",
    "TCP", "UDP", "WebSocket", "gRPC", "Docker Compose",
]

SKILL_HEADINGS = (
    "核心技能", "专业技能", "技术栈", "技能清单", "技能", "technical skills",
    "skills", "tech stack", "technologies",
)
SKILL_CATEGORIES = {
    "language", "framework", "database", "middleware", "platform", "devops",
    "tool", "graphics", "protocol", "architecture", "concept", "other",
}
CATEGORY_ALIASES = {
    "library": "framework", "libraries": "framework", "runtime": "framework",
    "db": "database", "databases": "database", "storage": "database",
    "cloud": "platform", "os": "platform", "operating-system": "platform",
    "devops/tool": "devops", "tools": "tool", "build": "tool", "ide": "tool",
    "networking": "protocol", "network": "protocol", "messaging": "middleware",
    "concurrency": "concept", "data": "database", "graphics/rendering": "graphics",
}
CATEGORY_OVERRIDES = {
    "qml": "framework",
    "qt quick": "framework",
    "qt widgets": "framework",
    "qt webengine": "framework",
    "stl": "framework",
    "occt": "framework",
    "cef": "framework",
    "qss": "graphics",
    "qpainter": "graphics",
    "opengl": "graphics",
    "mvp": "architecture",
    "lockfreequeue": "concept",
    "mysql": "database",
    "sqlite": "database",
    "windows": "platform",
    "linux": "platform",
    "tcp": "protocol",
    "udp": "protocol",
    "udp 组播": "protocol",
    "visual studio": "tool",
    "vs code": "tool",
    "cmake": "tool",
    "qmake": "tool",
    "git": "devops",
}
CANONICAL_NAMES = {
    "c++11/14/17/20": "C++",
    "c++11": "C++",
    "c++14": "C++",
    "c++17": "C++",
    "c++20": "C++",
    "qt 5/6": "Qt",
    "qt5": "Qt",
    "qt6": "Qt",
    "qt quick/qml": "Qt Quick",
    "mvp 架构": "MVP",
    "lockfreequeue 无锁队列": "LockFreeQueue",
    "visual studio code": "VS Code",
}
GENERIC_SKILLS = {
    "团队管理", "项目管理", "需求分析", "文档编写", "敏捷研发", "敏捷开发",
    "跨团队协作", "沟通", "招聘", "交付", "迭代计划", "进度管控", "任务拆分",
    "技术方案", "问题定位", "管理", "pmp", "军工", "航天", "舰载",
}
FIELD_TECH_HINTS = [term.lower() for term in TECH_TERMS] + [
    "qt", "qml", "c++", "java", "sql", "tcp", "udp", "cmake", "qtcreator",
    "framework", "database", "redis", "linux", "windows", "opengl", "stl",
]


class ProfileService:
    def __init__(self, db: Database, llm: LLMService) -> None:
        self.db = db
        self.llm = llm

    def _ensure(self) -> None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT id FROM profile WHERE id = 1").fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO profile(id, name, headline, location, resume_text, links_json, updated_at) "
                    "VALUES (1, '', '', '', '', '{}', ?)",
                    (iso(),),
                )

    def get(self) -> dict[str, Any]:
        self._ensure()
        with self.db.connect() as conn:
            profile = conn.execute("SELECT * FROM profile WHERE id = 1").fetchone()
            skills = conn.execute(
                "SELECT * FROM skills ORDER BY level DESC, name COLLATE NOCASE"
            ).fetchall()
            goals = conn.execute(
                "SELECT * FROM goals ORDER BY CASE status WHEN 'active' THEN 0 ELSE 1 END, "
                "priority ASC, updated_at DESC"
            ).fetchall()
            suggestions = conn.execute(
                "SELECT * FROM profile_suggestions WHERE status = 'pending' ORDER BY created_at DESC"
            ).fetchall()
        return {
            "name": profile["name"],
            "headline": profile["headline"],
            "location": profile["location"],
            "resume_text": profile["resume_text"],
            "resume_filename": profile["resume_filename"],
            "links": json_loads(profile["links_json"], {}),
            "updated_at": profile["updated_at"],
            "skills": [dict(row) for row in skills],
            "goals": [dict(row) for row in goals],
            "suggestions": [dict(row) for row in suggestions],
            "completeness": self.completeness(),
        }

    def completeness(self) -> int:
        with self.db.connect() as conn:
            profile = conn.execute("SELECT resume_text, headline, name FROM profile WHERE id = 1").fetchone()
            skills = conn.execute("SELECT COUNT(*) AS n FROM skills").fetchone()["n"]
            short_goals = conn.execute(
                "SELECT COUNT(*) AS n FROM goals WHERE kind = 'short' AND status = 'active'"
            ).fetchone()["n"]
            long_goals = conn.execute(
                "SELECT COUNT(*) AS n FROM goals WHERE kind = 'long' AND status = 'active'"
            ).fetchone()["n"]
        checks = [
            bool(profile and (profile["name"] or profile["headline"])),
            bool(profile and profile["resume_text"].strip()),
            skills >= 3,
            short_goals >= 1,
            long_goals >= 1,
        ]
        return round(sum(checks) / len(checks) * 100)

    def update(self, patch: dict[str, Any], source: str = "user") -> dict[str, Any]:
        self._ensure()
        allowed = {"name", "headline", "location", "resume_text", "resume_filename"}
        with self.db.connect() as conn:
            current = conn.execute("SELECT * FROM profile WHERE id = 1").fetchone()
            for field in allowed:
                if field not in patch:
                    continue
                new_value = str(patch[field] or "")
                old_value = current[field] or ""
                if new_value == old_value:
                    continue
                conn.execute(f"UPDATE profile SET {field} = ? WHERE id = 1", (new_value,))
                conn.execute(
                    "INSERT INTO profile_revisions(field, old_value, new_value, source, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (field, old_value, new_value, source, iso()),
                )
            if "links" in patch and isinstance(patch["links"], dict):
                conn.execute(
                    "UPDATE profile SET links_json = ? WHERE id = 1",
                    (json_dumps(patch["links"]),),
                )
            conn.execute("UPDATE profile SET updated_at = ? WHERE id = 1", (iso(),))
        return self.get()

    def add_skill(
        self,
        name: str,
        level: int = 2,
        years: float | None = None,
        notes: str = "",
        source: str = "user",
        category: str = "",
    ) -> dict[str, Any]:
        slug = normalize_tag(name)
        if not slug:
            raise ValueError("Skill name is empty")
        level = max(0, min(4, int(level)))
        category = _normalize_category(category, name)
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO skills(id, name, category, slug, level, years, notes, source, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(slug) DO UPDATE SET
                    name = excluded.name, category = excluded.category, level = excluded.level,
                    years = excluded.years, notes = excluded.notes, source = excluded.source,
                    updated_at = excluded.updated_at
                """,
                (
                    stable_id("skill", slug), name.strip()[:80], category, slug, level,
                    years, notes, source, iso(),
                ),
            )
            row = conn.execute("SELECT * FROM skills WHERE slug = ?", (slug,)).fetchone()
        return dict(row)

    def update_skill(self, skill_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        fields = []
        values: list[Any] = []
        for field in ("name", "category", "level", "years", "notes"):
            if field not in patch:
                continue
            value = patch[field]
            if field == "level":
                value = max(0, min(4, int(value)))
            elif field == "years" and value is not None:
                value = max(0.0, float(value))
            elif field == "category":
                value = _normalize_category(value, str(patch.get("name") or ""))
            fields.append(f"{field} = ?")
            values.append(value)
        if not fields:
            with self.db.connect() as conn:
                row = conn.execute("SELECT * FROM skills WHERE id = ?", (skill_id,)).fetchone()
            if row is None:
                raise KeyError(skill_id)
            return dict(row)
        values.extend([iso(), skill_id])
        with self.db.connect() as conn:
            conn.execute(f"UPDATE skills SET {', '.join(fields)}, updated_at = ? WHERE id = ?", values)
            row = conn.execute("SELECT * FROM skills WHERE id = ?", (skill_id,)).fetchone()
        if row is None:
            raise KeyError(skill_id)
        return dict(row)

    def delete_skill(self, skill_id: str) -> None:
        with self.db.connect() as conn:
            conn.execute("DELETE FROM skills WHERE id = ?", (skill_id,))

    def add_goal(
        self,
        kind: str,
        title: str,
        description: str = "",
        status: str = "active",
        due_date: str | None = None,
        priority: int = 2,
    ) -> dict[str, Any]:
        if kind not in {"short", "long"}:
            raise ValueError("Goal kind must be short or long")
        if not title.strip():
            raise ValueError("Goal title is empty")
        goal_id = stable_id("goal", f"{kind}:{title}:{iso()}")
        now = iso()
        with self.db.connect() as conn:
            conn.execute(
                "INSERT INTO goals(id, kind, title, description, status, due_date, priority, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (goal_id, kind, title.strip()[:160], description, status, due_date, priority, now, now),
            )
            conn.execute(
                "INSERT INTO audit_log(kind, ref_id, detail_json, created_at) VALUES (?, ?, ?, ?)",
                ("goal_created", goal_id, json_dumps({"kind": kind, "title": title}), now),
            )
            row = conn.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()
        return dict(row)

    def update_goal(self, goal_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        with self.db.connect() as conn:
            current = conn.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()
            if current is None:
                raise KeyError(goal_id)
            fields = []
            values: list[Any] = []
            for field in ("kind", "title", "description", "status", "due_date", "priority"):
                if field not in patch:
                    continue
                if field == "kind" and patch[field] not in {"short", "long"}:
                    raise ValueError("Goal kind must be short or long")
                fields.append(f"{field} = ?")
                values.append(patch[field])
            if fields:
                values.extend([iso(), goal_id])
                conn.execute(f"UPDATE goals SET {', '.join(fields)}, updated_at = ? WHERE id = ?", values)
                conn.execute(
                    "INSERT INTO audit_log(kind, ref_id, detail_json, created_at) VALUES (?, ?, ?, ?)",
                    (
                        "goal_updated",
                        goal_id,
                        json_dumps({"before": dict(current), "after": patch}),
                        iso(),
                    ),
                )
            row = conn.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()
        return dict(row)

    def delete_goal(self, goal_id: str) -> None:
        with self.db.connect() as conn:
            conn.execute("DELETE FROM goals WHERE id = ?", (goal_id,))

    def collection_terms(self) -> list[str]:
        profile = self.get()
        terms = [skill["name"] for skill in profile["skills"]]
        for goal in profile["goals"]:
            if goal["status"] == "active":
                terms.append(goal["title"])
                if goal["description"]:
                    terms.append(goal["description"])
        return unique([term.strip() for term in terms if term and term.strip()])

    def suggest_from_resume(self, text: str) -> list[dict[str, Any]]:
        text = (text or "").strip()
        if not text:
            return []
        suggestions: list[dict[str, Any]] = []
        if self.llm.available:
            context = _skill_context(text)
            try:
                result = self.llm.complete_json(
                    "你是技术栈抽取器。只输出 JSON，不要解释。"
                    "从片段中提取明确出现的技术栈，排除软技能、职责描述和管理内容。"
                    "类别限定：language,framework,database,middleware,platform,devops,"
                    "tool,graphics,protocol,architecture,concept,other。"
                    'JSON 格式：{"skills":[{"name":"规范技术名","category":"...",'
                    '"level":0-4,"evidence":"原文短句"}]}。'
                    "最多 30 项；evidence 必须逐字来自片段。",
                    f"简历技能片段：\n{context}",
                    max_tokens=8000,
                    effort="low",
                )
                raw_items = (result or {}).get("skills", [])
                if isinstance(raw_items, list):
                    for item in raw_items:
                        suggestion = _normalize_suggestion(item, text)
                        if suggestion:
                            suggestions.append(suggestion)
            except Exception:
                suggestions = []
        if not suggestions:
            lowered = text.lower()
            for term in TECH_TERMS:
                if re.search(rf"(?<![\w]){re.escape(term.lower())}(?![\w])", lowered):
                    suggestions.append(
                        {
                            "name": term,
                            "category": _normalize_category("", term),
                            "level": 2,
                            "evidence": _evidence(text, term),
                        }
                    )
        existing = {skill["slug"] for skill in self.get()["skills"]}
        output: list[dict[str, Any]] = []
        seen_names: set[str] = set()
        with self.db.connect() as conn:
            for suggestion in suggestions[:30]:
                slug = normalize_tag(suggestion["name"])
                if not slug or slug in existing or slug in seen_names:
                    continue
                seen_names.add(slug)
                sid = stable_id("sugg", slug)
                conn.execute(
                    "INSERT INTO profile_suggestions(id, kind, name, category, level, evidence, status, created_at) "
                    "VALUES (?, 'skill', ?, ?, ?, ?, 'pending', ?) "
                    "ON CONFLICT(id) DO UPDATE SET name = excluded.name, category = excluded.category, "
                    "level = excluded.level, evidence = excluded.evidence, status = 'pending'",
                    (
                        sid,
                        suggestion["name"],
                        suggestion["category"],
                        suggestion["level"],
                        suggestion["evidence"],
                        iso(),
                    ),
                )
                output.append({"id": sid, **suggestion, "status": "pending"})
        return output

    def save_resume(self, text: str, filename: str | None = None) -> dict[str, Any]:
        self.update({"resume_text": text, "resume_filename": filename or ""}, source="resume")
        with self.db.connect() as conn:
            conn.execute("DELETE FROM profile_suggestions WHERE kind = 'skill' AND status = 'pending'")
        suggestions = self.suggest_from_resume(text)
        return {"suggestions": suggestions, "profile": self.get()}

    def accept_suggestion(self, suggestion_id: str) -> dict[str, Any]:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM profile_suggestions WHERE id = ?", (suggestion_id,)
            ).fetchone()
            if row is None:
                raise KeyError(suggestion_id)
            conn.execute(
                "UPDATE profile_suggestions SET status = 'accepted' WHERE id = ?", (suggestion_id,)
            )
        return self.add_skill(
            row["name"],
            level=row["level"] or 2,
            source="resume",
            category=row["category"] if "category" in row.keys() else "",
        )

    def dismiss_suggestion(self, suggestion_id: str) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE profile_suggestions SET status = 'dismissed' WHERE id = ?", (suggestion_id,)
            )

    def accept_all_suggestions(self) -> dict[str, Any]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM profile_suggestions WHERE kind = 'skill' AND status = 'pending'"
            ).fetchall()
        accepted = 0
        for row in rows:
            self.add_skill(
                row["name"],
                level=row["level"] or 2,
                source="resume",
                category=row["category"] if "category" in row.keys() else "",
            )
            with self.db.connect() as conn:
                conn.execute(
                    "UPDATE profile_suggestions SET status = 'accepted' WHERE id = ?", (row["id"],)
                )
            accepted += 1
        return {"accepted": accepted, "profile": self.get()}

    def clear_suggestions(self) -> None:
        with self.db.connect() as conn:
            conn.execute("DELETE FROM profile_suggestions WHERE kind = 'skill' AND status = 'pending'")


def _evidence(text: str, term: str, width: int = 100) -> str:
    index = text.lower().find(term.lower())
    if index < 0:
        return ""
    start = max(0, index - width // 2)
    end = min(len(text), index + len(term) + width // 2)
    return re.sub(r"\s+", " ", text[start:end]).strip()


def _skill_context(text: str, max_chars: int = 6000) -> str:
    """Prefer the resume's explicit skills section to reduce model reasoning."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").splitlines()
    for index, line in enumerate(lines):
        stripped = line.strip()
        lowered = stripped.lower()
        if not any(heading.lower() in lowered for heading in SKILL_HEADINGS):
            continue
        if stripped.startswith("#"):
            level = len(stripped) - len(stripped.lstrip("#"))
        else:
            level = 0
        end = len(lines)
        for cursor in range(index + 1, len(lines)):
            candidate = lines[cursor].strip()
            if candidate.startswith("#"):
                next_level = len(candidate) - len(candidate.lstrip("#"))
                if level == 0 or next_level <= level:
                    end = cursor
                    break
        section = "\n".join(lines[index:end]).strip()
        if len(section) >= 40:
            return section[:max_chars]
    selected = [
        line for line in lines
        if any(hint in line.lower() for hint in FIELD_TECH_HINTS)
    ]
    if selected:
        return "\n".join(selected)[:max_chars]
    return text[:max_chars]


def _normalize_suggestion(item: Any, resume_text: str) -> dict[str, Any] | None:
    if isinstance(item, str):
        name, category, level, evidence = item, "", 2, ""
    elif isinstance(item, dict):
        name = item.get("name") or item.get("skill") or ""
        category = item.get("category") or ""
        level = item.get("level", 2)
        evidence = item.get("evidence") or item.get("quote") or ""
    else:
        return None
    name = re.sub(r"^[\s\-*•\d.、]+", "", str(name)).strip("*` ")[:80]
    if not name:
        return None
    canonical = CANONICAL_NAMES.get(name.lower(), name)
    if canonical.lower() in GENERIC_SKILLS or name.lower() in GENERIC_SKILLS:
        return None
    if not _text_contains(resume_text, canonical) and not _text_contains(resume_text, name):
        return None
    try:
        level_value = max(0, min(4, int(level)))
    except (TypeError, ValueError):
        level_value = 2
    evidence_text = re.sub(r"\s+", " ", str(evidence)).strip()[:300]
    compact_resume = re.sub(r"\s+", "", resume_text).lower()
    if evidence_text and re.sub(r"\s+", "", evidence_text).lower() not in compact_resume:
        evidence_text = _evidence(resume_text, canonical)
    if not evidence_text:
        evidence_text = _evidence(resume_text, canonical) or _evidence(resume_text, name)
    if not evidence_text:
        return None
    return {
        "name": canonical,
        "category": _normalize_category(category, canonical),
        "level": level_value,
        "evidence": evidence_text,
    }


def _normalize_category(category: Any, name: str) -> str:
    name_key = (name or "").strip().lower()
    if name_key in CATEGORY_OVERRIDES:
        return CATEGORY_OVERRIDES[name_key]
    value = str(category or "").strip().lower()
    value = CATEGORY_ALIASES.get(value, value)
    if value in SKILL_CATEGORIES:
        return value
    lowered = (name or "").lower()
    if any(term in lowered for term in ("c++", "python", "java", "rust", "go", "sql", "typescript", "javascript", "kotlin", "swift")):
        return "language"
    if any(term in lowered for term in ("mysql", "sqlite", "postgres", "mongo", "redis", "database", "存储")):
        return "database"
    if any(term in lowered for term in ("linux", "windows", "android", "ios", "platform", "平台")):
        return "platform"
    if any(term in lowered for term in ("qt", "spring", "react", "vue", "framework", "库")):
        return "framework"
    if any(term in lowered for term in ("tcp", "udp", "http", "websocket", "grpc", "protocol", "协议")):
        return "protocol"
    if any(term in lowered for term in ("opengl", "qpainter", "渲染", "绘制", "图像")):
        return "graphics"
    if any(term in lowered for term in ("git", "cmake", "qmake", "visual studio", "vs code", "docker", "kubernetes")):
        return "tool"
    if any(term in lowered for term in ("线程", "并发", "架构", "内存", "队列", "设计")):
        return "concept"
    return "other"


def _text_contains(text: str, term: str) -> bool:
    term = (term or "").strip()
    if not term:
        return False
    lowered = text.lower()
    if re.fullmatch(r"[\w+#./_-]+", term.lower()):
        return re.search(
            rf"(?<![\w]){re.escape(term.lower())}(?![\w])", lowered
        ) is not None
    return term.lower() in lowered
