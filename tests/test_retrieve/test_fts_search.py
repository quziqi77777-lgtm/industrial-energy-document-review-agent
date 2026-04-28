"""维度检索测试。"""

from __future__ import annotations

import pytest

from src.retrieve import search_standards_for_dimension, tokenize_for_fts
from src.store.repository import Repository


@pytest.fixture
def repo_with_standards() -> Repository:
    r = Repository(":memory:")
    r.upsert_standard(
        "QSY1217_3.1",
        "QSY1217",
        "3.1",
        "管道分级",
        "天然气长输管道每30公里划分一个区段；输油管道每20公里划分一个区段。",
        ["E1_staffing", "管道分级"],
    )
    r.upsert_standard(
        "QSY1217_3.2",
        "QSY1217",
        "3.2",
        "巡线工配置",
        "每3-10公里配置1名管道巡线工。",
        ["E1_staffing"],
    )
    r.upsert_standard(
        "GBT1.1_8.8",
        "GBT1.1",
        "8.8",
        "缩略词",
        "首次出现的缩略词应给出中文全称注释。",
        ["C3_language"],
    )
    r.upsert_standard(
        "TSG31_3.2",
        "TSG31",
        "3.2",
        "管道压力",
        "压力管道分级与设计压力。",
        ["C2_content_completeness"],
    )
    yield r
    r.close()


def test_tokenize_returns_string() -> None:
    out = tokenize_for_fts("天然气管道每30公里划分一个区段")
    assert isinstance(out, str)
    assert "管道" in out


def test_dimension_filter_excludes_other_standards(repo_with_standards: Repository) -> None:
    """E1 维度只能命中 QSY1217 的条款。"""
    results = search_standards_for_dimension(
        repo=repo_with_standards,
        dimension="E1_staffing",
        query="管道",
        top_k=10,
    )
    assert all(r["standard_name"] == "QSY1217" for r in results)
    assert len(results) >= 1


def test_dimension_filter_keeps_relevant(repo_with_standards: Repository) -> None:
    results = search_standards_for_dimension(
        repo=repo_with_standards,
        dimension="C2_content_completeness",
        query="压力",
        top_k=10,
    )
    names = {r["standard_name"] for r in results}
    assert names <= {"TSG31", "GBT21246", "QSY1217"}


def test_unknown_dimension_no_filter(repo_with_standards: Repository) -> None:
    """未声明的维度：不加白名单过滤，全库搜。"""
    results = search_standards_for_dimension(
        repo=repo_with_standards,
        dimension="X_unknown",
        query="管道",
        top_k=10,
    )
    assert len(results) >= 2


def test_top_k_limit(repo_with_standards: Repository) -> None:
    results = search_standards_for_dimension(
        repo=repo_with_standards,
        dimension="E1_staffing",
        query="管道",
        top_k=1,
    )
    assert len(results) <= 1
