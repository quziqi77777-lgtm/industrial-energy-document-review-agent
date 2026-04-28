"""Agent 基础设施：结果模型、JSON 提取、Provider 注入。"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from src.llm import LLMProvider


log = logging.getLogger(__name__)


@dataclass
class Finding:
    severity: str           # "high" | "medium" | "low"
    description: str
    evidence: str = ""
    rule_id: str | None = None
    chunk_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "description": self.description,
            "evidence": self.evidence,
            "rule_id": self.rule_id,
            "chunk_id": self.chunk_id,
        }


@dataclass
class AgentResult:
    """单维度审核结果。

    与 sample_label_result.json 字段对齐：
    - verdict: pass / partial / fail / uncertain
    - score: 维度分数（int）
    - confidence: 置信度 0-100
    - findings: 问题清单
    - extra: 维度专属结构化字段（如 staffing_analysis）
    """

    dimension: str
    verdict: str = "uncertain"
    score: int | None = None
    confidence: int = 0
    findings: list[Finding] = field(default_factory=list)
    details: str = ""
    extra: dict[str, Any] = field(default_factory=dict)
    need_human_review: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension,
            "verdict": self.verdict,
            "score": self.score,
            "confidence": self.confidence,
            "findings": [f.to_dict() for f in self.findings],
            "details": self.details,
            "extra": self.extra,
            "need_human_review": self.need_human_review,
        }


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def parse_json_response(text: str, *, strict: bool = False) -> dict[str, Any]:
    """从 LLM 输出里提取首个 JSON 对象。

    支持：
    - ```json ... ``` 围栏
    - 直接 `{...}`
    - 文本中夹带的 JSON 块（找首个平衡的 `{...}`）

    `strict=True` 时无法解析就抛 ValueError；否则返回 {}。
    """
    if not text:
        if strict:
            raise ValueError("空响应")
        return {}

    m = _JSON_FENCE_RE.search(text)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start : i + 1]
                    try:
                        return json.loads(candidate)
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)

    if strict:
        raise ValueError(f"无法从响应中提取 JSON: {text[:200]}")
    return {}


class BaseAgent:
    """所有维度 Agent 的基类。"""

    dimension: str = ""

    def __init__(
        self,
        provider: LLMProvider,
        text_model: str,
        *,
        temperature: float = 0.0,
    ) -> None:
        self.provider = provider
        self.text_model = text_model
        self.temperature = temperature

    def run(self, *args, **kwargs) -> AgentResult:  # pragma: no cover
        raise NotImplementedError
