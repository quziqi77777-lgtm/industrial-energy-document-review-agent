"""T1-T3 metrics 测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.chunk.models import Chunk, ChunkType
from src.metrics import (
    MetricsContext,
    compute_metrics,
    compute_t1_template,
    compute_t2_format,
    compute_t3_latency,
)


def _heading(title: str, idx: int = 0) -> Chunk:
    return Chunk(
        chunk_id=f"d__h__{idx}",
        doc_id="d",
        chunk_type=ChunkType.HEADING,
        section_path=str(idx),
        title=title,
        content=title,
        paragraph_index=idx,
        anchor_text=title[:10],
    )


# ---------------------------------------------------------------------------
# T1
# ---------------------------------------------------------------------------


class TestT1Template:
    def test_full_template_passes(self) -> None:
        chunks = [
            _heading("第一章 岗位职责", 1),
            _heading("第二章 作业流程", 2),
            _heading("第三章 巡检规范", 3),
            _heading("第四章 应急处置", 4),
            _heading("第五章 培训管理", 5),
        ]
        result = compute_t1_template(chunks)
        assert result.verdict == "pass"
        assert result.score == 4
        assert result.extra["template_match"]["hit"] == 5

    def test_partial_match(self) -> None:
        chunks = [
            _heading("第一章 岗位职责", 1),
            _heading("第二章 作业流程", 2),
            _heading("第三章 巡检规范", 3),
            _heading("第四章 应急处置", 4),
            # 缺培训
        ]
        result = compute_t1_template(chunks)
        assert result.verdict == "partial"
        assert result.score == 3

    def test_fails_minimal(self) -> None:
        chunks = [_heading("简介", 1)]
        result = compute_t1_template(chunks)
        assert result.verdict == "fail"
        assert result.score == 0

    def test_accepts_chunk_dicts(self) -> None:
        """compute_t1_template 必须既能吃 Chunk 也能吃 dict。"""
        chunks = [
            {"title": "岗位职责", "chunk_type": "heading"},
            {"title": "作业流程", "chunk_type": "heading"},
            {"title": "巡检规范", "chunk_type": "heading"},
            {"title": "应急处置", "chunk_type": "heading"},
            {"title": "培训管理", "chunk_type": "heading"},
        ]
        result = compute_t1_template(chunks)
        assert result.score == 4

    def test_ignores_non_heading(self) -> None:
        chunks = [
            {"title": "岗位职责 作业流程 巡检 应急 培训", "chunk_type": "text"},
        ]
        result = compute_t1_template(chunks)
        # text 内容不算
        assert result.extra["template_match"]["hit"] == 0


# ---------------------------------------------------------------------------
# T2
# ---------------------------------------------------------------------------


class TestT2Format:
    @pytest.mark.parametrize("fmt", [".doc", ".docx", ".pdf", ".PDF"])
    def test_supported_formats_pass(self, fmt: str) -> None:
        result = compute_t2_format(fmt, parse_succeeded=True)
        assert result.verdict == "pass"
        assert result.score == 4

    def test_unsupported_format_fails(self) -> None:
        result = compute_t2_format(".rtf", parse_succeeded=True)
        assert result.verdict == "fail"
        assert result.findings[0].severity == "high"

    def test_parse_failure_fails(self) -> None:
        result = compute_t2_format(".docx", parse_succeeded=False)
        assert result.verdict == "fail"
        assert "解析失败" in result.findings[0].description


# ---------------------------------------------------------------------------
# T3
# ---------------------------------------------------------------------------


class TestT3Latency:
    @pytest.mark.parametrize(
        "elapsed,expected_score,expected_verdict",
        [
            (10.0, 4, "pass"),
            (60.0, 4, "pass"),
            (90.0, 3, "pass"),
            (120.0, 3, "pass"),
            (150.0, 2, "partial"),
            (180.0, 2, "partial"),
            (200.0, 1, "partial"),
            (240.0, 1, "partial"),
            (300.0, 0, "fail"),
        ],
    )
    def test_tiers(self, elapsed: float, expected_score: int, expected_verdict: str) -> None:
        result = compute_t3_latency(elapsed)
        assert result.score == expected_score
        assert result.verdict == expected_verdict

    def test_negative_clamped(self) -> None:
        result = compute_t3_latency(-1.0)
        assert result.score == 4

    def test_extra_carries_elapsed(self) -> None:
        result = compute_t3_latency(45.678)
        assert result.extra["elapsed_seconds"] == 45.678


# ---------------------------------------------------------------------------
# 总入口
# ---------------------------------------------------------------------------


class TestComputeMetrics:
    def test_returns_three_dimensions(self, tmp_path: Path) -> None:
        ctx = MetricsContext(
            doc_path=tmp_path / "x.docx",
            chunks=[_heading("岗位职责", 1), _heading("作业流程", 2),
                    _heading("巡检", 3), _heading("应急", 4),
                    _heading("培训", 5)],
            elapsed_seconds=42.0,
            input_format=".docx",
        )
        out = compute_metrics(ctx)
        assert set(out.keys()) == {"T1_template", "T2_format", "T3_latency"}
        assert out["T1_template"].verdict == "pass"
        assert out["T2_format"].verdict == "pass"
        assert out["T3_latency"].verdict == "pass"

    def test_failed_pdf_propagates(self, tmp_path: Path) -> None:
        ctx = MetricsContext(
            doc_path=tmp_path / "x.pdf",
            chunks=[],
            elapsed_seconds=300.0,
            input_format=".pdf",
            parse_succeeded=False,
        )
        out = compute_metrics(ctx)
        assert out["T2_format"].verdict == "fail"
        assert out["T3_latency"].verdict == "fail"
