"""E1_staffing —— 人员配备审核维度。

赛题规则（赛题审核规则与补充说明.pdf 第 95-106 行）：
- 每 100 公里设置 1 名安全工程师（CEILING）
- 天然气管道每 30 公里划分 1 个区段
- 输油管道每 20 公里划分 1 个区段
- 每个区段设 1 名专职区段长
- 每 3-10 公里配 1 名管道巡线工

实现策略（评审 align_rules + rule_then_llm）：
- LLM 仅做"从段落中抽取数字"的小任务（2B 模型也能干）
- 公式判定走规则代码：结果可重复、可解释，准确率 100%
- 抽不到数字时，verdict=uncertain 让人工兜底
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from typing import Any

from src.agents.base import AgentResult, BaseAgent, Finding, parse_json_response
from src.llm import Message


log = logging.getLogger(__name__)


# ---------- 数据模型 ----------


@dataclass
class StaffingFacts:
    """从文档中抽取的事实。"""

    total_employees: int | None = None
    safety_engineers_actual: int | None = None
    pipeline_count: int = 0
    pipeline_names: list[str] = field(default_factory=list)
    pipeline_length_km: float = 0.0
    pipeline_kinds: list[str] = field(default_factory=list)  # ["natural_gas", "oil"]
    section_supervisors_actual: int | None = None
    patrol_workers_actual: int | None = None

    def is_complete_enough(self) -> bool:
        """够不够走公式判定。"""
        return (
            self.total_employees is not None
            and self.pipeline_length_km > 0
            and self.patrol_workers_actual is not None
        )


# ---------- 规则推理 ----------


def evaluate_staffing(facts: StaffingFacts) -> tuple[list[dict[str, Any]], list[Finding]]:
    """根据公式判定：返回 (rules, findings)。

    rules: 每条公式的输入/计算/通过状态（写入 staffing_analysis.extra）
    findings: 不满足规则的问题清单
    """
    rules: list[dict[str, Any]] = []
    findings: list[Finding] = []

    # 规则 1：安全工程师 ≥ ceil(total / 100)
    if facts.total_employees is not None and facts.safety_engineers_actual is not None:
        required = max(1, math.ceil(facts.total_employees / 100))
        passed = facts.safety_engineers_actual >= required
        rules.append(
            {
                "rule": "safety_engineer",
                "formula": "CEILING(total_employees / 100)",
                "required": required,
                "actual": facts.safety_engineers_actual,
                "passed": passed,
            }
        )
        if not passed:
            findings.append(
                Finding(
                    severity="high",
                    description=(
                        f"安全工程师配置不足：实际 {facts.safety_engineers_actual} 人 "
                        f"< 应配 {required} 人（按总员工 {facts.total_employees}）"
                    ),
                    evidence=f"total_employees={facts.total_employees}",
                    rule_id="E1_staffing.safety_engineer",
                )
            )

    # 规则 2：区段划分（天然气 30km，输油 20km）→ 每段 1 区段长
    if facts.pipeline_length_km > 0 and facts.pipeline_kinds:
        # 简化：若同时有天然气和输油，分别算；这里取最严的（30km 划分）作示例
        # 更严谨需要按管线分别统计长度，留待 production agent 做
        if "natural_gas" in facts.pipeline_kinds:
            ng_segments = math.ceil(facts.pipeline_length_km / 30)
        else:
            ng_segments = 0
        if "oil" in facts.pipeline_kinds and "natural_gas" not in facts.pipeline_kinds:
            oil_segments = math.ceil(facts.pipeline_length_km / 20)
        else:
            oil_segments = 0
        required_supervisors = ng_segments + oil_segments
        if facts.section_supervisors_actual is not None and required_supervisors > 0:
            passed = facts.section_supervisors_actual >= required_supervisors
            rules.append(
                {
                    "rule": "section_supervisor",
                    "formula": "CEILING(length_km / segment_size_km)",
                    "required": required_supervisors,
                    "actual": facts.section_supervisors_actual,
                    "passed": passed,
                }
            )
            if not passed:
                findings.append(
                    Finding(
                        severity="medium",
                        description=(
                            f"专职区段长不足：实际 {facts.section_supervisors_actual} "
                            f"< 应配 {required_supervisors}"
                        ),
                        rule_id="E1_staffing.section_supervisor",
                    )
                )

    # 规则 3：巡线工 → 每人覆盖 3-10 km
    if facts.pipeline_length_km > 0 and facts.patrol_workers_actual:
        km_per_worker = facts.pipeline_length_km / facts.patrol_workers_actual
        passed = 3.0 <= km_per_worker <= 10.0
        rules.append(
            {
                "rule": "patrol_worker",
                "formula": "length_km / patrol_workers_actual ∈ [3, 10]",
                "km_per_worker": round(km_per_worker, 2),
                "actual": facts.patrol_workers_actual,
                "length_km": facts.pipeline_length_km,
                "passed": passed,
            }
        )
        if not passed:
            severity = "high" if km_per_worker > 10.0 else "low"
            findings.append(
                Finding(
                    severity=severity,
                    description=(
                        f"巡线工配置异常：每人 {km_per_worker:.2f} km，应在 [3, 10] 区间"
                    ),
                    rule_id="E1_staffing.patrol_worker",
                )
            )

    return rules, findings


def derive_verdict(
    rules: list[dict[str, Any]],
    findings: list[Finding],
    facts: StaffingFacts,
) -> tuple[str, int, int]:
    """根据规则结果决定 verdict + score + confidence。

    - 全部规则 passed → pass，score 满分
    - 有 high finding → fail
    - 有 medium/low → partial
    - 无可用规则 → uncertain
    """
    if not rules or not facts.is_complete_enough():
        return "uncertain", 0, 30

    failed_count = sum(1 for r in rules if not r.get("passed"))
    if failed_count == 0:
        return "pass", 14, 95
    has_high = any(f.severity == "high" for f in findings)
    if has_high:
        return "fail", 5, 80
    return "partial", 10, 75


# ---------- LLM 抽取 ----------


_EXTRACTION_SYSTEM = """你是一个专业文档信息抽取助手。从用户提供的工业文档片段中，抽取人员配备相关的数字事实。
只输出严格 JSON，不要解释、不要 markdown 围栏。

JSON 字段（缺失填 null）：
{
  "total_employees": int | null,             // 员工总数
  "safety_engineers_actual": int | null,     // 安全/HSE 工程师实际人数
  "pipeline_count": int,                     // 管道条数
  "pipeline_names": [str],                   // 管道名称列表
  "pipeline_length_km": float,               // 管道总里程（公里）
  "pipeline_kinds": [str],                   // ["natural_gas"|"oil"|"refined_oil"]
  "section_supervisors_actual": int | null,  // 专职区段长实际人数
  "patrol_workers_actual": int | null        // 巡线工实际人数
}

抽取规则：
- 数字必须有原文支撑，不要编造
- 长度单位换算到 km
- 多条管道，length 相加为总长
- 天然气=natural_gas, 成品油/输油=refined_oil（按 oil 处理）"""


def build_extraction_prompt(chunks: list[dict[str, Any]]) -> list[Message]:
    text = "\n---\n".join(c.get("content", "") for c in chunks if c.get("content"))
    text = text[:6000]   # 防超长，2B 模型上限
    user = f"以下是文档中与人员/管道相关的段落：\n\n{text}\n\n请抽取人员配备字段，输出 JSON。"
    return [
        Message(role="system", content=_EXTRACTION_SYSTEM),
        Message(role="user", content=user),
    ]


# ---------- 数字回退抽取（不靠 LLM）----------

_NUM = r"(\d+(?:\.\d+)?)"
_PATTERNS = {
    "total_employees": [
        re.compile(rf"(?:现有|共有|总计)\s*员工\s*{_NUM}\s*(?:名|人)"),
        re.compile(rf"员工\s*(?:总数|共)\s*(?:为|约|计)?\s*{_NUM}\s*(?:名|人)?"),
        re.compile(rf"员工人数\s*(?:为|约|计)?\s*{_NUM}"),
        re.compile(rf"共\s*(?:有)?\s*{_NUM}\s*(?:名|人)\s*员工"),
    ],
    "patrol_workers_actual": [
        re.compile(rf"配置\s*巡(?:护|线)(?:工|人员|员)\s*{_NUM}"),
        re.compile(rf"巡(?:护|线)(?:工|人员|员)\s*{_NUM}\s*(?:名|人)"),
    ],
    "safety_engineers_actual": [
        re.compile(rf"(?:HSE|安全)(?:管理)?\s*工程师\s*{_NUM}\s*(?:名|人)"),
    ],
    "pipeline_length_km": [
        re.compile(
            rf"(?:总长|管辖长度|全线长度|管道里程|管线长度|线路总长|"
            rf"管辖管道|管辖范围)(?:合计|共计|总计)?\s*(?:约|共|计|为)?\s*{_NUM}\s*"
            rf"(?:公里|km|千米)",
            re.IGNORECASE,
        ),
        re.compile(rf"所辖管道[^\d]{{0,20}}{_NUM}\s*(?:公里|km|千米)", re.IGNORECASE),
        re.compile(rf"={_NUM}\s*(?:km|公里|千米)", re.IGNORECASE),
    ],
}


def regex_fallback_extract(chunks: list[dict[str, Any]]) -> StaffingFacts:
    """无 LLM 时的兜底抽取。"""
    facts = StaffingFacts()
    text = "\n".join(c.get("content", "") for c in chunks)

    for field_name, patterns in _PATTERNS.items():
        for pat in patterns:
            m = pat.search(text)
            if m:
                value: Any = float(m.group(1))
                if field_name != "pipeline_length_km":
                    value = int(value)
                setattr(facts, field_name, value)
                break

    if "天然气" in text:
        facts.pipeline_kinds.append("natural_gas")
    if "成品油" in text or "输油" in text:
        facts.pipeline_kinds.append("oil")

    return facts


def merge_facts(llm_facts: StaffingFacts, fallback: StaffingFacts) -> StaffingFacts:
    """LLM 抽到的优先；缺的字段用 regex 兜底补。"""
    out = StaffingFacts(
        total_employees=llm_facts.total_employees or fallback.total_employees,
        safety_engineers_actual=llm_facts.safety_engineers_actual
        or fallback.safety_engineers_actual,
        pipeline_count=llm_facts.pipeline_count or fallback.pipeline_count,
        pipeline_names=llm_facts.pipeline_names or fallback.pipeline_names,
        pipeline_length_km=llm_facts.pipeline_length_km or fallback.pipeline_length_km,
        pipeline_kinds=llm_facts.pipeline_kinds or fallback.pipeline_kinds,
        section_supervisors_actual=llm_facts.section_supervisors_actual
        or fallback.section_supervisors_actual,
        patrol_workers_actual=llm_facts.patrol_workers_actual
        or fallback.patrol_workers_actual,
    )
    return out


# ---------- Agent ----------


class E1StaffingAgent(BaseAgent):
    dimension = "E1_staffing"

    def run(self, chunks: list[dict[str, Any]]) -> AgentResult:
        """跑完整 E1 流程。

        Args:
            chunks: 文档相关 chunks（建议预过滤"岗位/管道/巡护"段落 + 表格）

        Returns:
            AgentResult，含 staffing_analysis 维度专属字段。
        """
        # 1) LLM 抽取
        llm_facts = self._llm_extract(chunks)

        # 2) Regex 兜底
        fallback = regex_fallback_extract(chunks)

        # 3) 合并
        facts = merge_facts(llm_facts, fallback)

        # 4) 公式判定
        rules, findings = evaluate_staffing(facts)

        # 5) verdict
        verdict, score, confidence = derive_verdict(rules, findings, facts)

        # 6) 输出
        result = AgentResult(
            dimension=self.dimension,
            verdict=verdict,
            score=score,
            confidence=confidence,
            findings=findings,
            details=self._build_details(facts, rules),
            extra={
                "staffing_analysis": {
                    "total_employees": facts.total_employees,
                    "safety_engineers_actual": facts.safety_engineers_actual,
                    "pipeline_count": facts.pipeline_count,
                    "pipeline_names": facts.pipeline_names,
                    "pipeline_length_km": facts.pipeline_length_km,
                    "pipeline_kinds": facts.pipeline_kinds,
                    "patrol_workers": facts.patrol_workers_actual,
                    "section_supervisors": facts.section_supervisors_actual,
                    "km_per_worker": (
                        round(facts.pipeline_length_km / facts.patrol_workers_actual, 2)
                        if facts.patrol_workers_actual
                        else None
                    ),
                },
                "rules": rules,
            },
            need_human_review=(verdict == "uncertain"),
        )
        return result

    def _llm_extract(self, chunks: list[dict[str, Any]]) -> StaffingFacts:
        try:
            messages = build_extraction_prompt(chunks)
            response = self.provider.call_text(
                messages,
                model=self.text_model,
                temperature=self.temperature,
                max_tokens=512,
            )
            data = parse_json_response(response)
            return _facts_from_dict(data)
        except Exception as e:
            log.warning("E1 LLM 抽取失败，回退到规则: %s", e)
            return StaffingFacts()

    @staticmethod
    def _build_details(facts: StaffingFacts, rules: list[dict[str, Any]]) -> str:
        if not rules:
            return "可用证据不足，无法做公式判定。"
        passed = [r["rule"] for r in rules if r.get("passed")]
        failed = [r["rule"] for r in rules if not r.get("passed")]
        return (
            f"总员工 {facts.total_employees}，"
            f"管道总里程 {facts.pipeline_length_km} km，"
            f"巡线工 {facts.patrol_workers_actual}。"
            f" 规则通过：{passed or '无'}；规则未过：{failed or '无'}。"
        )


def _facts_from_dict(data: dict[str, Any]) -> StaffingFacts:
    """容错地把 dict 转成 StaffingFacts。"""

    def _i(k: str) -> int | None:
        v = data.get(k)
        try:
            return int(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    def _f(k: str) -> float:
        v = data.get(k)
        try:
            return float(v) if v is not None else 0.0
        except (TypeError, ValueError):
            return 0.0

    def _list(k: str) -> list[str]:
        v = data.get(k)
        return [str(x) for x in v] if isinstance(v, list) else []

    return StaffingFacts(
        total_employees=_i("total_employees"),
        safety_engineers_actual=_i("safety_engineers_actual"),
        pipeline_count=_i("pipeline_count") or 0,
        pipeline_names=_list("pipeline_names"),
        pipeline_length_km=_f("pipeline_length_km"),
        pipeline_kinds=_list("pipeline_kinds"),
        section_supervisors_actual=_i("section_supervisors_actual"),
        patrol_workers_actual=_i("patrol_workers_actual"),
    )
