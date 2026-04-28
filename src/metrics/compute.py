"""T1-T3 指标计算。

赛题原文：
- T1 模板使用：识别是否使用规定模板
- T2 兼容性：模型需兼容 WORD 和 PDF 格式
- T3 识别效率：
    第一档 ≤60s    → 4 分（满）
    第二档 ≤120s   → 3 分
    第三档 ≤180s   → 2 分
    第四档 >240s   → 0 分
    （180-240 s 之间按规则书没有明示，按线性插值给 1 分）

设计：T1-T3 不是 agent，是 pipeline 计算的运行时指标。
返回结构与 AgentResult 一致以便 pipeline 统一处理。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.agents.base import AgentResult, Finding


log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 上下文
# ---------------------------------------------------------------------------


@dataclass
class MetricsContext:
    """pipeline 跑完后传给 metrics 的所有运行时信息。"""

    doc_path: Path
    chunks: list[Any]                  # list[Chunk]
    elapsed_seconds: float
    input_format: str                  # ".docx" / ".doc" / ".pdf"
    parse_succeeded: bool = True
    extra: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# T1 模板
# ---------------------------------------------------------------------------

# 作业指导书的标准模板章节顺序（赛题规则书：岗位条件、职责、作业指引、巡检、
# 操作规范、应急、培训）
TEMPLATE_SECTIONS: list[tuple[str, list[str]]] = [
    ("岗位/职责", ["岗位", "职责", "组织"]),
    ("作业指引/操作", ["作业", "操作", "指引", "流程"]),
    ("巡检", ["巡检", "巡线", "巡视", "检查"]),
    ("应急", ["应急", "事故", "处置", "抢险"]),
    ("培训", ["培训", "教育", "考核"]),
]


def compute_t1_template(chunks: list[Any]) -> AgentResult:
    """T1 模板使用度。

    标准：作业指导书核心 5 个模板章节命中数。
    全命中给满分；命中 ≥4 给 partial；<4 给 fail。
    """
    titles = []
    for c in chunks:
        title = getattr(c, "title", None) or (c.get("title") if isinstance(c, dict) else "")
        chunk_type = getattr(c, "chunk_type", None) or (c.get("chunk_type") if isinstance(c, dict) else "")
        type_str = chunk_type.value if hasattr(chunk_type, "value") else str(chunk_type or "")
        if type_str == "heading" and title:
            titles.append(title)

    titles_blob = " ".join(titles)
    matched: list[str] = []
    missed: list[str] = []
    for canonical, synonyms in TEMPLATE_SECTIONS:
        if any(kw in titles_blob for kw in synonyms):
            matched.append(canonical)
        else:
            missed.append(canonical)

    total = len(TEMPLATE_SECTIONS)
    hit = len(matched)

    findings: list[Finding] = []
    if missed:
        findings.append(
            Finding(
                severity="medium" if hit < 4 else "low",
                description=f"模板章节未命中：{missed}",
                rule_id="T1.template_match",
            )
        )

    if hit == total:
        verdict, score = "pass", 4
    elif hit >= 4:
        verdict, score = "partial", 3
    elif hit >= 2:
        verdict, score = "partial", 2
    else:
        verdict, score = "fail", 0

    return AgentResult(
        dimension="T1_template",
        verdict=verdict,
        score=score,
        confidence=90,
        findings=findings,
        details=f"命中模板章节 {hit}/{total}：{matched}",
        extra={"template_match": {"hit": hit, "total": total, "missed": missed}},
    )


# ---------------------------------------------------------------------------
# T2 兼容性
# ---------------------------------------------------------------------------


SUPPORTED_FORMATS = {".doc", ".docx", ".pdf"}


def compute_t2_format(input_format: str, parse_succeeded: bool) -> AgentResult:
    """T2 格式兼容性。

    规则简单：输入是 .doc/.docx/.pdf 且 parse 成功 → pass。
    """
    fmt = input_format.lower()
    findings: list[Finding] = []
    if fmt not in SUPPORTED_FORMATS:
        findings.append(
            Finding(
                severity="high",
                description=f"不支持的文件格式：{fmt}",
                rule_id="T2.format",
            )
        )
        return AgentResult(
            dimension="T2_format",
            verdict="fail", score=0, confidence=100,
            findings=findings,
            details=f"不支持格式 {fmt}",
            extra={"input_format": fmt},
        )

    if not parse_succeeded:
        findings.append(
            Finding(
                severity="high",
                description=f"格式 {fmt} 解析失败",
                rule_id="T2.parse_failure",
            )
        )
        return AgentResult(
            dimension="T2_format",
            verdict="fail", score=0, confidence=100,
            findings=findings,
            details="解析失败",
            extra={"input_format": fmt},
        )

    return AgentResult(
        dimension="T2_format",
        verdict="pass", score=4, confidence=100,
        findings=[],
        details=f"格式 {fmt} 解析成功",
        extra={"input_format": fmt},
    )


# ---------------------------------------------------------------------------
# T3 识别效率
# ---------------------------------------------------------------------------


# 赛题档次：单位秒
T3_TIERS = [
    (60.0, 4, "第一档（≤60s）"),
    (120.0, 3, "第二档（≤120s）"),
    (180.0, 2, "第三档（≤180s）"),
    (240.0, 1, "第四档（≤240s）"),
]


def compute_t3_latency(elapsed_seconds: float) -> AgentResult:
    """T3 识别效率。"""
    elapsed = max(0.0, float(elapsed_seconds))

    score = 0
    label = "超时（>240s）"
    for limit, tier_score, tier_label in T3_TIERS:
        if elapsed <= limit:
            score = tier_score
            label = tier_label
            break

    if score >= 3:
        verdict = "pass"
    elif score >= 1:
        verdict = "partial"
    else:
        verdict = "fail"

    findings: list[Finding] = []
    if score < 3:
        findings.append(
            Finding(
                severity="medium" if score >= 1 else "high",
                description=f"识别耗时 {elapsed:.1f}s，落在 {label}",
                rule_id="T3.latency",
            )
        )

    return AgentResult(
        dimension="T3_latency",
        verdict=verdict,
        score=score,
        confidence=100,
        findings=findings,
        details=f"耗时 {elapsed:.2f}s，{label}",
        extra={"elapsed_seconds": round(elapsed, 3), "tier": label},
    )


# ---------------------------------------------------------------------------
# 总入口
# ---------------------------------------------------------------------------


def compute_metrics(ctx: MetricsContext) -> dict[str, AgentResult]:
    """跑 T1-T3，返回 dimension → AgentResult 字典。"""
    return {
        "T1_template": compute_t1_template(ctx.chunks),
        "T2_format": compute_t2_format(ctx.input_format, ctx.parse_succeeded),
        "T3_latency": compute_t3_latency(ctx.elapsed_seconds),
    }
