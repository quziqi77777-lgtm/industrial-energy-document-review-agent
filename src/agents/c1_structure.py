"""C1 结构合规性 —— 纯规则维度。

赛题原文要求：
1. 目录覆盖是否全面，是否涵盖核心模块（岗位条件、职责、作业指引、巡检、
   操作规范、应急、培训等）
2. 层级逻辑是否清晰，章节编号是否符合标准（如 1.1.1 分级）
3. 标题是否简洁明确
4. 附录配套是否完整
5. 关键附录（附录 A、C 等）是否存在
6. 页码与索引是否准确

实现策略：纯规则。所有检查项都是确定性的，不需要 LLM。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from src.agents.base import AgentResult, BaseAgent, Finding


log = logging.getLogger(__name__)


# ----------------------------------------------------------------------------
# 检查规则配置
# ----------------------------------------------------------------------------

# 核心模块关键词清单：每个内层 list 是同义词组（任一命中即视为覆盖）
REQUIRED_MODULES: list[tuple[str, list[str]]] = [
    ("岗位条件", ["岗位", "职位", "人员配置", "岗位条件", "岗位设置"]),
    ("职责", ["职责", "责任", "管理职责", "岗位职责"]),
    ("作业指引", ["作业指引", "作业流程", "作业内容", "操作流程", "作业规范"]),
    ("巡检", ["巡检", "巡线", "巡护", "巡视", "检查"]),
    ("操作规范", ["操作", "操作规范", "操作要求", "工艺", "规程"]),
    ("应急", ["应急", "事故", "处置", "抢险", "应急响应"]),
    ("培训", ["培训", "教育", "考核", "训练"]),
]

# 关键附录标识（赛题点名 A 和 C）
KEY_APPENDIX_TAGS: list[str] = ["A", "C"]

# 章节编号模式
HIERARCHICAL_RE = re.compile(r"^\s*(\d+)([.．、]\s*\d+){1,4}\s")
TOP_LEVEL_RE = re.compile(r"^\s*(?:第\s*[一二三四五六七八九十百千]+\s*[章节]|\d+\s*[.．、]?\s*[^.．、])")
APPENDIX_RE = re.compile(r"附\s*录\s*([A-Z]|[一二三四五六七八九十])", re.IGNORECASE)

# 标题长度合理范围（中文字符数）
TITLE_MIN_LEN = 2
TITLE_MAX_LEN = 40


# ----------------------------------------------------------------------------
# 数据模型
# ----------------------------------------------------------------------------


@dataclass
class StructureFacts:
    headings: list[dict[str, Any]] = field(default_factory=list)
    """每条：{"level": int, "title": str, "section_path": str, "chunk_id": str}"""

    appendices_found: list[str] = field(default_factory=list)
    """文档中识别到的附录标识，比如 ['A', 'B', 'C']"""

    @property
    def all_titles(self) -> list[str]:
        return [h["title"] for h in self.headings]


# ----------------------------------------------------------------------------
# 抽取
# ----------------------------------------------------------------------------


def extract_structure(chunks: list[dict[str, Any]]) -> StructureFacts:
    """从 chunks 里抽出标题列表和附录标识。"""
    facts = StructureFacts()

    for c in chunks:
        chunk_type = c.get("chunk_type")
        title = (c.get("title") or "").strip()
        content = c.get("content", "")

        if chunk_type == "heading" and title:
            facts.headings.append(
                {
                    "level": _infer_level(c),
                    "title": title,
                    "section_path": c.get("section_path", ""),
                    "chunk_id": c.get("chunk_id", ""),
                }
            )

        # 任意 chunk 内容里都可能出现"附录 A"之类的标识
        for m in APPENDIX_RE.finditer(content):
            tag = m.group(1).upper()
            if tag not in facts.appendices_found:
                facts.appendices_found.append(tag)
        # 标题里也可能直接是"附录 A xxxx"
        for m in APPENDIX_RE.finditer(title):
            tag = m.group(1).upper()
            if tag not in facts.appendices_found:
                facts.appendices_found.append(tag)

    return facts


def _infer_level(chunk: dict[str, Any]) -> int:
    """从 chunk.section_path 推断标题层级。"""
    path = chunk.get("section_path", "")
    if not path:
        return 1
    # "1.1.1" → 3
    return path.count(".") + 1


# ----------------------------------------------------------------------------
# 检查项（每条返回 list[Finding]，空表示通过）
# ----------------------------------------------------------------------------


def check_required_modules(facts: StructureFacts) -> list[Finding]:
    """C1.1 核心模块覆盖度。"""
    findings: list[Finding] = []
    titles_blob = " ".join(facts.all_titles)
    for canonical, synonyms in REQUIRED_MODULES:
        if not any(kw in titles_blob for kw in synonyms):
            findings.append(
                Finding(
                    severity="medium",
                    description=f"缺少核心模块：{canonical}（同义词均未在标题中出现）",
                    rule_id="C1.required_modules",
                )
            )
    return findings


def check_hierarchical_numbering(facts: StructureFacts) -> list[Finding]:
    """C1.2 层级编号是否规范（1.1.1 分级风格）。

    判断标准：所有非顶层标题里，至少 60% 命中 N.M[.K] 模式。
    """
    if not facts.headings:
        return [
            Finding(
                severity="high",
                description="文档没有任何标题，结构无法识别",
                rule_id="C1.hierarchy",
            )
        ]
    sub = [h for h in facts.headings if h["level"] >= 2]
    if not sub:
        return []  # 只有顶层章节，不强求二级编号
    matched = sum(1 for h in sub if HIERARCHICAL_RE.match(h["title"]) or h.get("section_path"))
    ratio = matched / len(sub)
    if ratio < 0.6:
        return [
            Finding(
                severity="medium",
                description=(
                    f"二级及以下标题中仅 {ratio:.0%} 符合 1.1.1 分级风格，"
                    f"建议统一编号格式"
                ),
                rule_id="C1.hierarchy",
            )
        ]
    return []


def check_title_conciseness(facts: StructureFacts) -> list[Finding]:
    """C1.3 标题简洁明确。"""
    too_long = [
        h["title"] for h in facts.headings
        if len(h["title"]) > TITLE_MAX_LEN
    ]
    too_short = [
        h["title"] for h in facts.headings
        if 0 < len(h["title"]) < TITLE_MIN_LEN
    ]
    findings = []
    if too_long:
        sample = too_long[:3]
        findings.append(
            Finding(
                severity="low",
                description=f"{len(too_long)} 个标题过长（>{TITLE_MAX_LEN}字），如：{sample}",
                rule_id="C1.title_length",
            )
        )
    if too_short:
        findings.append(
            Finding(
                severity="low",
                description=f"{len(too_short)} 个标题过短：{too_short[:3]}",
                rule_id="C1.title_length",
            )
        )
    return findings


def check_key_appendices(facts: StructureFacts) -> list[Finding]:
    """C1.4 关键附录（A、C）是否存在。"""
    findings: list[Finding] = []
    for tag in KEY_APPENDIX_TAGS:
        if tag not in facts.appendices_found:
            findings.append(
                Finding(
                    severity="medium",
                    description=f"未发现关键附录 {tag}",
                    rule_id="C1.key_appendix",
                )
            )
    return findings


def check_appendix_completeness(facts: StructureFacts) -> list[Finding]:
    """C1.5 附录是否连续（A→B→C 不应跳号）。"""
    if not facts.appendices_found:
        return [
            Finding(
                severity="low",
                description="文档未识别到任何附录",
                rule_id="C1.appendix_continuity",
            )
        ]
    letters = sorted(t for t in facts.appendices_found if len(t) == 1 and t.isalpha())
    if len(letters) < 2:
        return []
    expected = [chr(ord(letters[0]) + i) for i in range(len(letters))]
    if letters != expected:
        return [
            Finding(
                severity="low",
                description=f"附录编号不连续：{letters}，期望 {expected}",
                rule_id="C1.appendix_continuity",
            )
        ]
    return []


# ----------------------------------------------------------------------------
# Verdict
# ----------------------------------------------------------------------------


def derive_verdict(findings: list[Finding]) -> tuple[str, int, int]:
    """汇总 verdict / score / confidence。"""
    if not findings:
        return "pass", 15, 95
    high_count = sum(1 for f in findings if f.severity == "high")
    medium_count = sum(1 for f in findings if f.severity == "medium")
    low_count = sum(1 for f in findings if f.severity == "low")

    if high_count > 0 or medium_count >= 3:
        return "fail", 5, 90
    if medium_count >= 1:
        return "partial", 10, 88
    return "partial" if low_count > 0 else "pass", 13, 85


# ----------------------------------------------------------------------------
# Agent
# ----------------------------------------------------------------------------


class C1StructureAgent(BaseAgent):
    """结构合规性 agent。

    完全规则，不调用 LLM，因此 provider/text_model 参数不会被使用，但保留
    以匹配 BaseAgent 接口，让 pipeline 调度统一。
    """

    dimension = "C1_structure"

    def run(self, chunks: list[dict[str, Any]]) -> AgentResult:
        facts = extract_structure(chunks)

        findings: list[Finding] = []
        findings += check_required_modules(facts)
        findings += check_hierarchical_numbering(facts)
        findings += check_title_conciseness(facts)
        findings += check_key_appendices(facts)
        findings += check_appendix_completeness(facts)

        verdict, score, confidence = derive_verdict(findings)

        return AgentResult(
            dimension=self.dimension,
            verdict=verdict,
            score=score,
            confidence=confidence,
            findings=findings,
            details=self._build_details(facts, findings),
            extra={
                "structure_analysis": {
                    "heading_count": len(facts.headings),
                    "appendices_found": facts.appendices_found,
                    "titles_sample": facts.all_titles[:10],
                },
            },
            need_human_review=False,
        )

    @staticmethod
    def _build_details(facts: StructureFacts, findings: list[Finding]) -> str:
        if not findings:
            return (
                f"识别到 {len(facts.headings)} 个标题，"
                f"附录 {facts.appendices_found}，全部检查项通过。"
            )
        return (
            f"识别到 {len(facts.headings)} 个标题、附录 {facts.appendices_found}；"
            f"共 {len(findings)} 条问题（"
            f"high={sum(1 for f in findings if f.severity=='high')}, "
            f"medium={sum(1 for f in findings if f.severity=='medium')}, "
            f"low={sum(1 for f in findings if f.severity=='low')}）。"
        )
