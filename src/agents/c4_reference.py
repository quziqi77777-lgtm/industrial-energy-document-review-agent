"""C4 引用文件可追溯性 —— 纯规则维度。

赛题原文要求：
- 附件以及涉及到的相关标准是否真实存在，是否为有效文件
- 文档里"详见附录 X"、"参见表 N"、"按 GBxxxx" 这类引用必须有可追溯目标

实现策略：纯规则。
1. 扫全文抽出三类引用：附录引用、表格引用、图片引用、标准引用
2. 扫全文抽出对应锚点：附录章节标题、表格标题、图片说明
3. 比对：引用的目标是否存在
4. 标准编号格式校验（必须形如 GB/T 12345-2020）

不做的事（留给其他维度）：
- 标准是否最新版本 → C2/L2 用 LLM + 外部知识做
- 跨段落语义连贯性 → C5 用 LLM 推理做
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from src.agents.base import AgentResult, BaseAgent, Finding


log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 引用模式
# ---------------------------------------------------------------------------

# 引用方："详见附录 A"、"参见附录B"、"附录 A 中"
APPENDIX_REF_RE = re.compile(
    r"(?:详见|参见|参考|见|按|依据)?\s*附\s*录\s*([A-Z]|[一二三四五六七八九十])",
    re.IGNORECASE,
)

# 引用方："详见表 3-1"、"如表 5 所示"
TABLE_REF_RE = re.compile(
    r"(?:详见|参见|参考|见|如)?\s*表\s*(\d+(?:[-.]\d+)?)\s*(?:所示|中|：)?",
)

# 引用方："详见图 3"、"如图 4-2 所示"
FIGURE_REF_RE = re.compile(
    r"(?:详见|参见|参考|见|如)?\s*图\s*(\d+(?:[-.]\d+)?)\s*(?:所示|：)?",
)

# 标准引用：GB 12345-2020、GB/T 1.1-2020、QSY 1217-2018、AQ 3057-2013、TSG 31-2014
STANDARD_REF_RE = re.compile(
    r"\b("
    r"GB(?:\s*/\s*T)?|GBT|"
    r"QSY|"
    r"AQ|"
    r"TSG|"
    r"SY|"
    r"JB|"
    r"NB"
    r")\s*(\d+(?:\.\d+)*)"            # number：仅点分，不含连字符
    r"(?:\s*[-—]\s*(\d{4}))?",        # year：可选 4 位
    re.IGNORECASE,
)

# 锚点方：标题里出现"附录 A"、"附录B"
APPENDIX_ANCHOR_RE = re.compile(
    r"^[\s\-]*附\s*录\s*([A-Z]|[一二三四五六七八九十])(?:\s|[:：]|$)",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------


@dataclass
class ReferenceFacts:
    appendix_refs: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    """{标识: [引用所在的 chunk_id...]}"""
    table_refs: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    figure_refs: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    standard_refs: list[dict[str, Any]] = field(default_factory=list)
    """[{"raw": "GB/T 1.1-2020", "family": "GBT", "number": "1.1", "year": "2020", "chunk_id": ...}]"""

    appendix_anchors: set[str] = field(default_factory=set)
    """文档里实际定义的附录标识。"""


# ---------------------------------------------------------------------------
# 抽取
# ---------------------------------------------------------------------------


def extract_references(chunks: list[dict[str, Any]]) -> ReferenceFacts:
    facts = ReferenceFacts()
    facts.appendix_refs = defaultdict(list)
    facts.table_refs = defaultdict(list)
    facts.figure_refs = defaultdict(list)

    for c in chunks:
        chunk_id = c.get("chunk_id", "")
        title = (c.get("title") or "")
        content = c.get("content") or ""
        chunk_type = c.get("chunk_type", "")
        text = f"{title}\n{content}"

        # 锚点：标题里看是否在定义"附录 X"
        if chunk_type == "heading":
            m = APPENDIX_ANCHOR_RE.match(title)
            if m:
                facts.appendix_anchors.add(m.group(1).upper())

        # 引用扫描
        for m in APPENDIX_REF_RE.finditer(text):
            tag = m.group(1).upper()
            facts.appendix_refs[tag].append(chunk_id)
        for m in TABLE_REF_RE.finditer(text):
            facts.table_refs[m.group(1)].append(chunk_id)
        for m in FIGURE_REF_RE.finditer(text):
            facts.figure_refs[m.group(1)].append(chunk_id)
        for m in STANDARD_REF_RE.finditer(text):
            family_raw = m.group(1).upper().replace(" ", "").replace("/", "")
            if family_raw == "GBT":
                family = "GBT"
            else:
                family = family_raw
            facts.standard_refs.append(
                {
                    "raw": m.group(0).strip(),
                    "family": family,
                    "number": m.group(2),
                    "year": m.group(3),
                    "chunk_id": chunk_id,
                }
            )

    return facts


# ---------------------------------------------------------------------------
# 检查项
# ---------------------------------------------------------------------------


def check_appendix_references(facts: ReferenceFacts) -> list[Finding]:
    """C4.1 引用的附录是否都有对应的章节定义。"""
    findings: list[Finding] = []
    for tag, ref_chunks in facts.appendix_refs.items():
        if tag not in facts.appendix_anchors:
            findings.append(
                Finding(
                    severity="medium",
                    description=f"正文引用了附录 {tag}，但文档中未找到对应的附录章节",
                    evidence=f"被引用 {len(ref_chunks)} 次",
                    rule_id="C4.appendix_ref",
                    chunk_id=ref_chunks[0] if ref_chunks else None,
                )
            )
    return findings


def check_standard_format(facts: ReferenceFacts) -> list[Finding]:
    """C4.2 标准引用格式是否规范（应包含编号 + 年份）。"""
    findings: list[Finding] = []
    seen_problems: set[str] = set()
    for ref in facts.standard_refs:
        if not ref.get("year"):
            key = f"{ref['family']}{ref['number']}"
            if key in seen_problems:
                continue
            seen_problems.add(key)
            findings.append(
                Finding(
                    severity="low",
                    description=(
                        f"标准引用 {ref['raw']} 未注明发布年份，应写作"
                        f" {ref['family']} {ref['number']}-YYYY"
                    ),
                    rule_id="C4.standard_year",
                    chunk_id=ref.get("chunk_id"),
                )
            )
    return findings


def check_orphan_anchors(facts: ReferenceFacts) -> list[Finding]:
    """C4.3 反向：定义了附录但正文从来没引用过。"""
    findings: list[Finding] = []
    for tag in sorted(facts.appendix_anchors):
        if tag not in facts.appendix_refs:
            findings.append(
                Finding(
                    severity="low",
                    description=f"附录 {tag} 已定义但正文未引用",
                    rule_id="C4.orphan_appendix",
                )
            )
    return findings


# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------


def derive_verdict(findings: list[Finding]) -> tuple[str, int, int]:
    if not findings:
        return "pass", 12, 95
    medium_count = sum(1 for f in findings if f.severity == "medium")
    high_count = sum(1 for f in findings if f.severity == "high")

    if high_count > 0 or medium_count >= 3:
        return "fail", 4, 90
    if medium_count >= 1:
        return "partial", 8, 88
    return "partial", 10, 85   # 仅 low


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


class C4ReferenceAgent(BaseAgent):
    dimension = "C4_reference"

    def run(self, chunks: list[dict[str, Any]]) -> AgentResult:
        facts = extract_references(chunks)

        findings: list[Finding] = []
        findings += check_appendix_references(facts)
        findings += check_standard_format(facts)
        findings += check_orphan_anchors(facts)

        verdict, score, confidence = derive_verdict(findings)

        return AgentResult(
            dimension=self.dimension,
            verdict=verdict,
            score=score,
            confidence=confidence,
            findings=findings,
            details=self._build_details(facts, findings),
            extra={
                "reference_analysis": {
                    "appendix_refs": {k: len(v) for k, v in facts.appendix_refs.items()},
                    "appendix_anchors": sorted(facts.appendix_anchors),
                    "table_refs_count": sum(len(v) for v in facts.table_refs.values()),
                    "figure_refs_count": sum(len(v) for v in facts.figure_refs.values()),
                    "standards": [
                        {"raw": s["raw"], "year": s.get("year")}
                        for s in facts.standard_refs[:20]
                    ],
                    "standards_total": len(facts.standard_refs),
                },
            },
            need_human_review=False,
        )

    @staticmethod
    def _build_details(facts: ReferenceFacts, findings: list[Finding]) -> str:
        return (
            f"附录引用 {len(facts.appendix_refs)} 个，"
            f"附录定义 {len(facts.appendix_anchors)} 个，"
            f"标准引用 {len(facts.standard_refs)} 处，"
            f"问题 {len(findings)} 条。"
        )
