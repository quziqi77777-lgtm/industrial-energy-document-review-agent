"""E1 人员配备 agent 测试。

覆盖：
- 公式判定（evaluate_staffing）
- LLM 抽取（用 MockProvider 注入固定 JSON）
- regex 兜底
- 集成：用真实 .doc 样本跑全链路
"""

from __future__ import annotations

import json

import pytest

from src.agents.e1_staffing import (
    E1StaffingAgent,
    StaffingFacts,
    derive_verdict,
    evaluate_staffing,
    merge_facts,
    regex_fallback_extract,
)
from src.llm.mock_provider import MockProvider


class TestEvaluateStaffing:
    def test_all_passed(self) -> None:
        facts = StaffingFacts(
            total_employees=120,
            safety_engineers_actual=2,
            pipeline_length_km=200,
            pipeline_kinds=["natural_gas"],
            section_supervisors_actual=10,    # 200/30 ≈ 7
            patrol_workers_actual=40,          # 200/40 = 5 km/p ∈ [3,10]
        )
        rules, findings = evaluate_staffing(facts)
        assert all(r["passed"] for r in rules)
        assert findings == []

    def test_safety_engineer_short(self) -> None:
        facts = StaffingFacts(
            total_employees=300,
            safety_engineers_actual=1,
            pipeline_length_km=100,
            patrol_workers_actual=30,
        )
        rules, findings = evaluate_staffing(facts)
        eng_rule = next(r for r in rules if r["rule"] == "safety_engineer")
        assert eng_rule["required"] == 3
        assert eng_rule["actual"] == 1
        assert eng_rule["passed"] is False
        assert any("安全工程师" in f.description for f in findings)

    def test_patrol_worker_too_few(self) -> None:
        """巡线工每人覆盖 > 10 km → 配置不足。"""
        facts = StaffingFacts(
            total_employees=10,
            pipeline_length_km=200,
            patrol_workers_actual=10,    # 20 km/p
        )
        rules, findings = evaluate_staffing(facts)
        patrol = next(r for r in rules if r["rule"] == "patrol_worker")
        assert patrol["passed"] is False
        assert any(f.severity == "high" for f in findings)

    def test_patrol_worker_too_many_low_severity(self) -> None:
        """巡线工每人覆盖 < 3 km → low 级别。"""
        facts = StaffingFacts(
            total_employees=10,
            pipeline_length_km=20,
            patrol_workers_actual=20,    # 1 km/p
        )
        rules, findings = evaluate_staffing(facts)
        assert any(f.severity == "low" for f in findings)

    def test_no_facts_no_rules(self) -> None:
        rules, findings = evaluate_staffing(StaffingFacts())
        assert rules == []
        assert findings == []


class TestDeriveVerdict:
    def test_pass(self) -> None:
        facts = StaffingFacts(
            total_employees=120,
            pipeline_length_km=300,
            patrol_workers_actual=60,
        )
        rules = [{"rule": "x", "passed": True}]
        verdict, _, _ = derive_verdict(rules, [], facts)
        assert verdict == "pass"

    def test_fail_on_high(self) -> None:
        from src.agents.base import Finding

        facts = StaffingFacts(
            total_employees=120,
            pipeline_length_km=300,
            patrol_workers_actual=60,
        )
        verdict, _, _ = derive_verdict(
            [{"rule": "x", "passed": False}],
            [Finding(severity="high", description="x")],
            facts,
        )
        assert verdict == "fail"

    def test_partial_on_medium(self) -> None:
        from src.agents.base import Finding

        facts = StaffingFacts(
            total_employees=120,
            pipeline_length_km=300,
            patrol_workers_actual=60,
        )
        verdict, _, _ = derive_verdict(
            [{"rule": "x", "passed": False}],
            [Finding(severity="medium", description="x")],
            facts,
        )
        assert verdict == "partial"

    def test_uncertain_when_facts_incomplete(self) -> None:
        verdict, _, conf = derive_verdict([], [], StaffingFacts())
        assert verdict == "uncertain"
        assert conf < 50


class TestRegexFallback:
    def test_extracts_employees(self) -> None:
        chunks = [{"content": "公司共有员工 34 人，包括管理人员。"}]
        facts = regex_fallback_extract(chunks)
        assert facts.total_employees == 34

    def test_extracts_patrol(self) -> None:
        chunks = [{"content": "配置巡护人员 69 名覆盖全线。"}]
        facts = regex_fallback_extract(chunks)
        assert facts.patrol_workers_actual == 69

    def test_extracts_pipeline_kinds(self) -> None:
        chunks = [{"content": "运营天然气管道及成品油管道两条。"}]
        facts = regex_fallback_extract(chunks)
        assert "natural_gas" in facts.pipeline_kinds
        assert "oil" in facts.pipeline_kinds


class TestE1StaffingAgent:
    def test_with_mock_llm(self) -> None:
        llm_response = json.dumps(
            {
                "total_employees": 120,
                "safety_engineers_actual": 2,
                "pipeline_count": 1,
                "pipeline_names": ["ZM干线"],
                "pipeline_length_km": 200,
                "pipeline_kinds": ["natural_gas"],
                "section_supervisors_actual": 8,
                "patrol_workers_actual": 30,
            }
        )
        provider = MockProvider(text_response=llm_response)
        agent = E1StaffingAgent(provider, text_model="Qwen3-2B")
        chunks = [{"content": "员工 120 人，巡护 30 名。"}]
        result = agent.run(chunks)
        assert result.dimension == "E1_staffing"
        assert result.verdict in ("pass", "partial")
        assert result.extra["staffing_analysis"]["total_employees"] == 120
        assert result.extra["staffing_analysis"]["pipeline_length_km"] == 200

    def test_falls_back_when_llm_returns_garbage(self) -> None:
        provider = MockProvider(text_response="抱歉无法解析")
        agent = E1StaffingAgent(provider, text_model="Qwen3-2B")
        chunks = [
            {"content": "公司共有员工 34 人。"},
            {"content": "巡护工 69 名覆盖全线。"},
            {"content": "管道总长 274 公里，含天然气和成品油。"},
        ]
        result = agent.run(chunks)
        analysis = result.extra["staffing_analysis"]
        assert analysis["total_employees"] == 34
        assert analysis["patrol_workers"] == 69
        assert analysis["pipeline_length_km"] == 274
        assert "natural_gas" in analysis["pipeline_kinds"]

    def test_uncertain_when_no_evidence(self) -> None:
        provider = MockProvider(text_response="{}")
        agent = E1StaffingAgent(provider, text_model="Qwen3-2B")
        chunks = [{"content": "无关内容。"}]
        result = agent.run(chunks)
        assert result.verdict == "uncertain"
        assert result.need_human_review is True


class TestMergeFacts:
    def test_llm_takes_precedence(self) -> None:
        llm = StaffingFacts(total_employees=10)
        fb = StaffingFacts(total_employees=20, patrol_workers_actual=5)
        merged = merge_facts(llm, fb)
        assert merged.total_employees == 10
        assert merged.patrol_workers_actual == 5

    def test_fallback_fills_gaps(self) -> None:
        llm = StaffingFacts()
        fb = StaffingFacts(total_employees=99)
        merged = merge_facts(llm, fb)
        assert merged.total_employees == 99
