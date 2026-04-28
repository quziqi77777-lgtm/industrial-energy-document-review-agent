"""C4 引用规范 agent 测试。"""

from __future__ import annotations

from typing import Any

import pytest

from src.agents.base import Finding
from src.agents.c4_reference import (
    APPENDIX_ANCHOR_RE,
    APPENDIX_REF_RE,
    C4ReferenceAgent,
    ReferenceFacts,
    STANDARD_REF_RE,
    check_appendix_references,
    check_orphan_anchors,
    check_standard_format,
    derive_verdict,
    extract_references,
)


def _h(title: str, section_path: str = "", chunk_id: str = "") -> dict[str, Any]:
    return {
        "chunk_id": chunk_id or f"d__h__{title}",
        "doc_id": "d",
        "chunk_type": "heading",
        "section_path": section_path,
        "title": title,
        "content": title,
    }


def _t(content: str, chunk_id: str = "tx") -> dict[str, Any]:
    return {
        "chunk_id": chunk_id,
        "doc_id": "d",
        "chunk_type": "text",
        "section_path": "1",
        "title": "",
        "content": content,
    }


# ---------------------------------------------------------------------------
# 正则 sanity
# ---------------------------------------------------------------------------


class TestRegex:
    @pytest.mark.parametrize(
        "text,tag",
        [
            ("详见附录 A 中所列", "A"),
            ("参见附录B操作票", "B"),
            ("依据附录 C", "C"),
            ("附录一 模板", "一"),
        ],
    )
    def test_appendix_ref(self, text: str, tag: str) -> None:
        m = APPENDIX_REF_RE.search(text)
        assert m and m.group(1).upper() == tag.upper()

    @pytest.mark.parametrize(
        "title,tag",
        [
            ("附录 A 操作票模板", "A"),
            ("附录B：巡检表", "B"),
            ("- 附录 C", "C"),
        ],
    )
    def test_appendix_anchor(self, title: str, tag: str) -> None:
        m = APPENDIX_ANCHOR_RE.match(title)
        assert m and m.group(1).upper() == tag

    @pytest.mark.parametrize(
        "text,family,number,year",
        [
            ("应符合 GB/T 1.1-2020 要求", "GBT", "1.1", "2020"),
            ("引用 QSY 1217-2018 第 3 条", "QSY", "1217", "2018"),
            ("依据 AQ 3057", "AQ", "3057", None),
            ("TSG31-2014", "TSG", "31", "2014"),
        ],
    )
    def test_standard_ref(self, text: str, family: str, number: str, year: str | None) -> None:
        m = STANDARD_REF_RE.search(text)
        assert m
        fam = m.group(1).upper().replace(" ", "").replace("/", "")
        if fam == "GBT":
            fam = "GBT"
        assert fam == family
        assert m.group(2) == number
        assert m.group(3) == year


# ---------------------------------------------------------------------------
# 抽取
# ---------------------------------------------------------------------------


class TestExtractReferences:
    def test_collects_appendix_refs(self) -> None:
        chunks = [_t("详见附录 A", "c1"), _t("参见附录 B 中模板", "c2")]
        facts = extract_references(chunks)
        assert "A" in facts.appendix_refs
        assert "B" in facts.appendix_refs
        assert facts.appendix_refs["A"] == ["c1"]

    def test_collects_appendix_anchors(self) -> None:
        chunks = [_h("附录 A 操作票模板"), _h("附录B 应急流程")]
        facts = extract_references(chunks)
        assert facts.appendix_anchors == {"A", "B"}

    def test_table_and_figure_refs(self) -> None:
        chunks = [_t("详见表 3-1 所示", "c1"), _t("如图 4 所示", "c2")]
        facts = extract_references(chunks)
        assert "3-1" in facts.table_refs
        assert "4" in facts.figure_refs

    def test_standard_refs(self) -> None:
        chunks = [
            _t("应符合 GB/T 1.1-2020 标准", "c1"),
            _t("依据 QSY1217-2018", "c2"),
            _t("引用 AQ 3057", "c3"),
        ]
        facts = extract_references(chunks)
        assert len(facts.standard_refs) == 3
        gbt = next(s for s in facts.standard_refs if s["family"] == "GBT")
        assert gbt["number"] == "1.1"
        assert gbt["year"] == "2020"


# ---------------------------------------------------------------------------
# 规则 1：附录引用追溯
# ---------------------------------------------------------------------------


class TestAppendixReferences:
    def test_all_resolved(self) -> None:
        facts = ReferenceFacts()
        facts.appendix_refs["A"] = ["c1"]
        facts.appendix_refs["B"] = ["c2"]
        facts.appendix_anchors = {"A", "B"}
        assert check_appendix_references(facts) == []

    def test_broken_reference(self) -> None:
        facts = ReferenceFacts()
        facts.appendix_refs["A"] = ["c1"]
        facts.appendix_refs["B"] = ["c2"]
        facts.appendix_anchors = {"A"}
        findings = check_appendix_references(facts)
        assert len(findings) == 1
        assert "B" in findings[0].description
        assert findings[0].severity == "medium"


# ---------------------------------------------------------------------------
# 规则 2：标准格式
# ---------------------------------------------------------------------------


class TestStandardFormat:
    def test_with_year_passes(self) -> None:
        facts = ReferenceFacts(standard_refs=[
            {"raw": "GB/T 1.1-2020", "family": "GBT", "number": "1.1", "year": "2020"},
        ])
        assert check_standard_format(facts) == []

    def test_missing_year_warns(self) -> None:
        facts = ReferenceFacts(standard_refs=[
            {"raw": "AQ 3057", "family": "AQ", "number": "3057", "year": None},
        ])
        findings = check_standard_format(facts)
        assert len(findings) == 1
        assert findings[0].severity == "low"

    def test_dedupe_same_problem(self) -> None:
        facts = ReferenceFacts(standard_refs=[
            {"raw": "AQ 3057", "family": "AQ", "number": "3057", "year": None},
            {"raw": "AQ3057", "family": "AQ", "number": "3057", "year": None},
        ])
        # 同一个 family+number 只报一次
        assert len(check_standard_format(facts)) == 1


# ---------------------------------------------------------------------------
# 规则 3：孤立锚点
# ---------------------------------------------------------------------------


class TestOrphanAnchors:
    def test_referenced_anchor_no_warn(self) -> None:
        facts = ReferenceFacts()
        facts.appendix_refs["A"] = ["c1"]
        facts.appendix_anchors = {"A"}
        assert check_orphan_anchors(facts) == []

    def test_orphan_warns(self) -> None:
        facts = ReferenceFacts()
        facts.appendix_anchors = {"A", "Z"}
        facts.appendix_refs["A"] = ["c1"]
        findings = check_orphan_anchors(facts)
        assert len(findings) == 1
        assert "Z" in findings[0].description


# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------


class TestDeriveVerdict:
    def test_pass(self) -> None:
        v, s, _ = derive_verdict([])
        assert v == "pass"
        assert s == 12

    def test_partial_one_medium(self) -> None:
        v, _, _ = derive_verdict([Finding(severity="medium", description="x")])
        assert v == "partial"

    def test_fail_three_medium(self) -> None:
        v, _, _ = derive_verdict([Finding(severity="medium", description=str(i)) for i in range(3)])
        assert v == "fail"


# ---------------------------------------------------------------------------
# Agent 集成
# ---------------------------------------------------------------------------


class TestC4Agent:
    def test_well_formed_doc(self) -> None:
        chunks = [
            _h("第一章 概述"),
            _t("应符合 GB/T 1.1-2020 标准", "c1"),
            _t("详见附录 A 操作票", "c2"),
            _h("附录 A 操作票模板"),
            _t("票据样例", "c3"),
        ]
        agent = C4ReferenceAgent(provider=None, text_model="N/A")  # type: ignore[arg-type]
        result = agent.run(chunks)
        assert result.dimension == "C4_reference"
        assert result.verdict in ("pass", "partial")
        assert result.extra["reference_analysis"]["appendix_anchors"] == ["A"]

    def test_broken_reference_fails(self) -> None:
        chunks = [
            _t("详见附录 A", "c1"),
            _t("参见附录 B", "c2"),
            _t("依据附录 C", "c3"),
            # 三个附录都没定义
        ]
        agent = C4ReferenceAgent(provider=None, text_model="N/A")  # type: ignore[arg-type]
        result = agent.run(chunks)
        assert result.verdict == "fail"

    def test_does_not_call_llm(self) -> None:
        from src.llm.mock_provider import MockProvider

        provider = MockProvider(text_response="NEVER")
        agent = C4ReferenceAgent(provider=provider, text_model="X")
        agent.run([_t("详见附录 A", "c1")])
        assert provider.text_calls == []
