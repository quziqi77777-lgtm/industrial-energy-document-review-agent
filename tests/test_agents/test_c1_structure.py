"""C1 结构合规性 agent 测试。

5 条规则各自独立测试，最后跑 agent 全流程。
"""

from __future__ import annotations

from typing import Any

import pytest

from src.agents.c1_structure import (
    APPENDIX_RE,
    C1StructureAgent,
    StructureFacts,
    check_appendix_completeness,
    check_hierarchical_numbering,
    check_key_appendices,
    check_required_modules,
    check_title_conciseness,
    derive_verdict,
    extract_structure,
)
from src.agents.base import Finding


def _h(title: str, level: int = 1, section_path: str = "", chunk_id: str = "") -> dict[str, Any]:
    """构造 heading chunk dict（模拟 Repository 输出格式）。"""
    return {
        "chunk_id": chunk_id or f"d__h__{title}",
        "doc_id": "d",
        "chunk_type": "heading",
        "section_path": section_path,
        "title": title,
        "content": title,
    }


def _t(content: str, section: str = "1") -> dict[str, Any]:
    return {
        "chunk_id": f"d__t__{section}",
        "doc_id": "d",
        "chunk_type": "text",
        "section_path": section,
        "title": "",
        "content": content,
    }


# ---------------------------------------------------------------------------
# 抽取
# ---------------------------------------------------------------------------


class TestExtractStructure:
    def test_collects_headings(self) -> None:
        chunks = [_h("第一章 概述"), _h("1.1 目的", section_path="1.1"), _t("正文")]
        facts = extract_structure(chunks)
        assert len(facts.headings) == 2
        assert facts.headings[0]["title"] == "第一章 概述"

    def test_finds_appendix_in_content(self) -> None:
        chunks = [_t("详见附录 A 中所列。")]
        facts = extract_structure(chunks)
        assert facts.appendices_found == ["A"]

    def test_finds_appendix_in_heading(self) -> None:
        chunks = [_h("附录B 操作票模板")]
        facts = extract_structure(chunks)
        assert "B" in facts.appendices_found

    def test_dedupes_appendices(self) -> None:
        chunks = [_t("详见附录 A"), _t("再次参考附录A 中"), _h("附录 A 模板")]
        facts = extract_structure(chunks)
        assert facts.appendices_found.count("A") == 1


# ---------------------------------------------------------------------------
# 规则 1：核心模块覆盖
# ---------------------------------------------------------------------------


class TestRequiredModules:
    def test_all_present(self) -> None:
        facts = StructureFacts(
            headings=[
                {"title": "岗位职责"}, {"title": "作业指引"}, {"title": "巡检流程"},
                {"title": "操作规范"}, {"title": "应急处置"}, {"title": "员工培训"},
            ]
        )
        # 注意：这里 7 个核心模块只覆盖了 6 个（缺"岗位条件"），但同义词组里
        # "岗位职责"既能命中"职责"也能命中"岗位条件"组里的"岗位"
        assert check_required_modules(facts) == []

    def test_missing_emergency(self) -> None:
        facts = StructureFacts(
            headings=[
                {"title": "岗位"}, {"title": "职责"}, {"title": "作业指引"},
                {"title": "巡检"}, {"title": "操作规范"}, {"title": "培训"},
                # 缺"应急"
            ]
        )
        findings = check_required_modules(facts)
        assert len(findings) == 1
        assert "应急" in findings[0].description

    def test_empty_headings_misses_all(self) -> None:
        findings = check_required_modules(StructureFacts())
        assert len(findings) == 7  # 全部缺


# ---------------------------------------------------------------------------
# 规则 2：层级编号
# ---------------------------------------------------------------------------


class TestHierarchicalNumbering:
    def test_no_headings_is_high(self) -> None:
        findings = check_hierarchical_numbering(StructureFacts())
        assert len(findings) == 1
        assert findings[0].severity == "high"

    def test_only_top_level_passes(self) -> None:
        facts = StructureFacts(
            headings=[
                {"title": "第一章 概述", "level": 1},
                {"title": "第二章 操作", "level": 1},
            ]
        )
        assert check_hierarchical_numbering(facts) == []

    def test_section_path_satisfies(self) -> None:
        """有 section_path 的二级标题视为符合编号。"""
        facts = StructureFacts(
            headings=[
                {"title": "目的", "level": 2, "section_path": "1.1"},
                {"title": "适用范围", "level": 2, "section_path": "1.2"},
            ]
        )
        assert check_hierarchical_numbering(facts) == []

    def test_low_ratio_flags(self) -> None:
        facts = StructureFacts(
            headings=[
                {"title": "目的", "level": 2, "section_path": ""},
                {"title": "适用范围", "level": 2, "section_path": ""},
                {"title": "1.3 引用文件", "level": 2, "section_path": "1.3"},
            ]
        )
        findings = check_hierarchical_numbering(facts)
        assert len(findings) == 1
        assert "1.1.1" in findings[0].description or "分级" in findings[0].description


# ---------------------------------------------------------------------------
# 规则 3：标题简洁
# ---------------------------------------------------------------------------


class TestTitleConciseness:
    def test_normal_titles(self) -> None:
        facts = StructureFacts(
            headings=[{"title": "应急处置"}, {"title": "1.1 目的"}]
        )
        assert check_title_conciseness(facts) == []

    def test_overly_long(self) -> None:
        long_title = "本章主要描述在压力管道发生意外破裂时巡线员应当采取的应急响应步骤包含上报抢修和现场处置"
        facts = StructureFacts(headings=[{"title": long_title}])
        findings = check_title_conciseness(facts)
        assert len(findings) == 1
        assert findings[0].severity == "low"


# ---------------------------------------------------------------------------
# 规则 4：关键附录
# ---------------------------------------------------------------------------


class TestKeyAppendices:
    def test_both_present(self) -> None:
        assert check_key_appendices(
            StructureFacts(appendices_found=["A", "B", "C"])
        ) == []

    def test_missing_a(self) -> None:
        findings = check_key_appendices(StructureFacts(appendices_found=["B", "C"]))
        assert len(findings) == 1
        assert "A" in findings[0].description

    def test_missing_both(self) -> None:
        findings = check_key_appendices(StructureFacts(appendices_found=["B"]))
        assert len(findings) == 2


# ---------------------------------------------------------------------------
# 规则 5：附录连续
# ---------------------------------------------------------------------------


class TestAppendixCompleteness:
    def test_continuous(self) -> None:
        assert check_appendix_completeness(
            StructureFacts(appendices_found=["A", "B", "C"])
        ) == []

    def test_skipped_b(self) -> None:
        findings = check_appendix_completeness(
            StructureFacts(appendices_found=["A", "C"])
        )
        assert len(findings) == 1

    def test_no_appendix_at_all(self) -> None:
        findings = check_appendix_completeness(StructureFacts(appendices_found=[]))
        assert len(findings) == 1
        assert findings[0].severity == "low"


# ---------------------------------------------------------------------------
# Verdict 汇总
# ---------------------------------------------------------------------------


class TestDeriveVerdict:
    def test_pass(self) -> None:
        verdict, score, _ = derive_verdict([])
        assert verdict == "pass"
        assert score == 15

    def test_fail_on_three_medium(self) -> None:
        findings = [Finding(severity="medium", description=str(i)) for i in range(3)]
        verdict, _, _ = derive_verdict(findings)
        assert verdict == "fail"

    def test_partial_on_one_medium(self) -> None:
        findings = [Finding(severity="medium", description="x")]
        verdict, _, _ = derive_verdict(findings)
        assert verdict == "partial"

    def test_only_low_partial(self) -> None:
        findings = [Finding(severity="low", description="x")]
        verdict, _, _ = derive_verdict(findings)
        assert verdict == "partial"


# ---------------------------------------------------------------------------
# Agent 集成
# ---------------------------------------------------------------------------


class TestC1Agent:
    def test_well_formed_doc_passes(self) -> None:
        chunks = [
            _h("第一章 岗位职责", section_path="1"),
            _h("1.1 作业指引", section_path="1.1"),
            _h("1.2 巡检流程", section_path="1.2"),
            _h("第二章 操作规范", section_path="2"),
            _h("2.1 应急处置", section_path="2.1"),
            _h("第三章 员工培训", section_path="3"),
            _t("详见附录 A 操作票", section="3"),
            _t("参见附录 B 应急流程", section="3"),
            _t("详见附录 C 巡检表", section="3"),
        ]
        agent = C1StructureAgent(provider=None, text_model="N/A")  # type: ignore[arg-type]
        result = agent.run(chunks)
        assert result.dimension == "C1_structure"
        assert result.verdict in ("pass", "partial")
        assert result.extra["structure_analysis"]["appendices_found"] == ["A", "B", "C"]

    def test_minimal_doc_fails(self) -> None:
        """只有一个标题、没有附录、缺核心模块 → 应该 fail。"""
        chunks = [_h("简介"), _t("内容很短")]
        agent = C1StructureAgent(provider=None, text_model="N/A")  # type: ignore[arg-type]
        result = agent.run(chunks)
        assert result.verdict == "fail"
        descriptions = " ".join(f.description for f in result.findings)
        assert "应急" in descriptions or "培训" in descriptions

    def test_no_llm_calls(self) -> None:
        """C1 是规则维度，绝对不能调 provider。"""
        from src.llm.mock_provider import MockProvider

        provider = MockProvider(text_response="SHOULD_NEVER_BE_CALLED")
        agent = C1StructureAgent(provider=provider, text_model="X")
        agent.run([_h("测试"), _t("内容")])
        assert provider.text_calls == []
        assert provider.vision_calls == []


# ---------------------------------------------------------------------------
# 正则 sanity
# ---------------------------------------------------------------------------


class TestAppendixRegex:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("详见附录 A", "A"),
            ("附录B操作票", "B"),
            ("附 录 C 巡检", "C"),
            ("附录一 模板", "一"),
        ],
    )
    def test_matches(self, text: str, expected: str) -> None:
        m = APPENDIX_RE.search(text)
        assert m is not None
        assert m.group(1).upper() == expected.upper()
