"""测试公用 fixtures。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def fixtures_dir(repo_root: Path) -> Path:
    return repo_root / "tests" / "fixtures"


@pytest.fixture(scope="session")
def docum_dir(repo_root: Path) -> Path:
    """赛题原始资料目录（只读）。"""
    return repo_root / "docum"


@pytest.fixture(scope="session")
def sample_doc_path(docum_dir: Path) -> Path:
    """场景一原始 .doc 样本。"""
    p = docum_dir / "AS作业区作业指导书(1).doc"
    if not p.exists():
        pytest.skip(f"样本不存在: {p}")
    return p


@pytest.fixture(scope="session")
def sample_docx_path(docum_dir: Path) -> Path:
    """场景二一区一案 .docx 样本。"""
    p = docum_dir / "豫洛阳-兰郑长干线-CPY-0790-BFGDGS-ZZSYQFGS.docx"
    if not p.exists():
        pytest.skip(f"样本不存在: {p}")
    return p


@pytest.fixture
def tmp_data_dir(tmp_path: Path) -> Path:
    d = tmp_path / "data"
    (d / "docs").mkdir(parents=True)
    (d / "images").mkdir(parents=True)
    (d / "results").mkdir(parents=True)
    return d
