"""Grounded question answering. Links are constructed and verified by the server."""

from __future__ import annotations

import re
from typing import Any

from .db import Database
from .history import QAHistoryService
from .interests import InterestService
from .llm import LLMRefusal, LLMService, LLMUnavailable, validate_citations
from .search import SearchService
from .vault import Vault

SYSTEM_GROUNDED = """你是用户的本地个人知识库助手。你只能依据 <evidence> 中提供的本地资料回答事实问题。
必须遵守：
1. 不得编造本地文档、路径、链接或证据中没有的事实。
2. 只引用给出的证据编号 C1、C2...，回答中不要自行生成 Markdown 链接。
3. 证据不足时，明确说“本地知识库中的资料不足以回答”，citations 返回空数组。
4. 区分“本地资料支持的内容”和“通用建议”。
输出严格 JSON，不要代码围栏：
{"answer": "中文回答", "citations": [{"id": "C1", "quote": "证据中的原文短句"}]}"""

SYSTEM_GENERAL = """你是用户的个人学习助手。本地知识库没有找到相关资料。
请给出简洁、准确的中文通用解答，并明确说明这是模型通用知识、不是本地资料。
输出严格 JSON，不要代码围栏：{"answer": "中文回答", "citations": []}"""


class QAService:
    def __init__(
        self,
        db: Database,
        vault: Vault,
        search: SearchService,
        llm: LLMService,
        interests: InterestService,
        history: QAHistoryService | None = None,
    ) -> None:
        self.db = db
        self.vault = vault
        self.search = search
        self.llm = llm
        self.interests = interests
        self.history = history

    def ask(
        self,
        question: str,
        mode: str = "auto",
        top_k: int = 8,
        group_id: str | None = None,
    ) -> dict[str, Any]:
        question = (question or "").strip()
        if not question:
            raise ValueError("Question is empty")
        evidence_rows = self.search.evidence(question, limit=max(3, min(top_k, 12)))
        evidence = []
        for index, row in enumerate(evidence_rows, start=1):
            evidence.append({**row, "citation_id": f"C{index}"})

        if not evidence:
            result = self._no_local_answer(question, mode)
        else:
            result = self._grounded_answer(question, evidence, mode)

        self.interests.record_interaction(
            "ask",
            question,
            meta={
                "mode": result["mode"],
                "grounded": result["grounded"],
                "evidence_count": len(evidence),
            },
        )
        result["local_note_count"] = len({item["note_id"] for item in evidence})
        if self.history is not None:
            result["history_id"] = self.history.save(question, result, group_id=group_id)
        return result

    def _grounded_answer(self, question: str, evidence: list[dict[str, Any]], mode: str) -> dict[str, Any]:
        if not self.llm.available:
            return self._extractive(question, evidence, reason="未配置模型，返回本地证据摘录")
        context = "\n\n".join(
            f"[{item['citation_id']}] 标题：{item['title']}\n路径：{item['path']}\n片段：{item['snippet']}"
            for item in evidence
        )
        prompt = f"<evidence>\n{context}\n</evidence>\n\n用户问题：{question}"
        try:
            parsed = self.llm.complete_json(
                SYSTEM_GROUNDED, prompt, max_tokens=1800, effort="high"
            )
        except (LLMUnavailable, LLMRefusal):
            return self._extractive(question, evidence, reason="模型调用失败，返回本地证据摘录")

        if not parsed:
            return self._extractive(question, evidence, reason="模型未返回可用结构，返回本地证据摘录")
        answer = str(parsed.get("answer") or "").strip()
        citations = validate_citations(parsed.get("citations") or [], evidence, self.vault)
        if not citations:
            if mode == "local":
                return {
                    "answer": "本地资料被检索到，但未能形成经过验证的引用，因此不输出确定性结论。请查看下方本地证据或换一种问法。",
                    "mode": "insufficient",
                    "grounded": False,
                    "citations": [],
                    "evidence": self._evidence_payload(evidence),
                }
            return {
                "answer": (answer or "本地资料相关性不足。")
                + "\n\n注意：该回答未能通过本地引用校验，请将其视为模型通用解释。",
                "mode": "partial",
                "grounded": False,
                "citations": [],
                "evidence": self._evidence_payload(evidence),
            }
        if not answer:
            return self._extractive(question, evidence, reason="模型未返回答案，返回本地证据摘录")
        return {
            "answer": answer,
            "mode": "grounded",
            "grounded": True,
            "citations": citations,
            "evidence": self._evidence_payload(evidence),
        }

    def _no_local_answer(self, question: str, mode: str) -> dict[str, Any]:
        if mode == "local":
            return {
                "answer": "本地知识库未找到与该问题相关的文档。当前为“仅用本地资料”模式，因此不提供模型通用解答。",
                "mode": "insufficient",
                "grounded": False,
                "citations": [],
                "evidence": [],
            }
        if not self.llm.available:
            return {
                "answer": "本地知识库未找到相关文档；当前也未配置模型，无法生成通用解答。可以更换关键词，或先采集相关资料。",
                "mode": "insufficient",
                "grounded": False,
                "citations": [],
                "evidence": [],
            }
        try:
            parsed = self.llm.complete_json(
                SYSTEM_GENERAL,
                f"用户问题：{question}",
                max_tokens=1400,
                effort="medium",
            )
            if parsed and parsed.get("answer"):
                return {
                    "answer": str(parsed["answer"]).strip(),
                    "mode": "general",
                    "grounded": False,
                    "citations": [],
                    "evidence": [],
                    "notice": "本地知识库未找到相关文档，以上为模型通用知识。",
                }
        except (LLMUnavailable, LLMRefusal):
            pass
        return {
            "answer": "本地知识库未找到相关文档，模型调用也未能完成。可以更换关键词，或先采集相关资料。",
            "mode": "insufficient",
            "grounded": False,
            "citations": [],
            "evidence": [],
        }

    def _extractive(self, question: str, evidence: list[dict[str, Any]], reason: str) -> dict[str, Any]:
        citations = validate_citations(
            [{"id": item["citation_id"]} for item in evidence[:4]], evidence, self.vault
        )
        lines = [
            f"本地知识库中找到 {len(evidence)} 条相关片段（{reason}）。以下内容未经过生成式改写：",
            "",
        ]
        for item in evidence[:4]:
            snippet = re.sub(r"\s+", " ", item["snippet"])[:260]
            lines.append(f"- [{item['citation_id']}] {item['title']}：{snippet}")
        return {
            "answer": "\n".join(lines),
            "mode": "extractive",
            "grounded": bool(citations),
            "citations": citations,
            "evidence": self._evidence_payload(evidence),
            "notice": "未使用模型生成结论；请以本地原文为准。",
        }

    def _evidence_payload(self, evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
        payload = []
        for item in evidence:
            note = self.vault.read_note(item["note_id"])
            payload.append(
                {
                    "citation_id": item["citation_id"],
                    "note_id": item["note_id"],
                    "title": item["title"],
                    "path": item["path"],
                    "snippet": item["snippet"][:400],
                    "app_url": f"/api/notes/{item['note_id']}",
                    "obsidian_uri": note["obsidian_uri"] if note else None,
                }
            )
        return payload
