"""`.doc` → `.docx` 转换测试。

依赖系统 LibreOffice；如果不存在，跳过集成测试但保留单元测试。
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from src.parse.doc_converter import (
    DocConversionError,
    _find_soffice,
    convert_doc_to_docx,
)


HAS_SOFFICE = shutil.which("soffice") or shutil.which("libreoffice")


def test_find_soffice_or_raises() -> None:
    if HAS_SOFFICE:
        assert _find_soffice()
    else:
        with pytest.raises(DocConversionError, match="LibreOffice"):
            _find_soffice()


def test_unsupported_extension(tmp_path) -> None:
    src = tmp_path / "x.txt"
    src.write_text("hello")
    with pytest.raises(DocConversionError, match="不支持"):
        convert_doc_to_docx(src)


def test_missing_source_raises(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        convert_doc_to_docx(tmp_path / "nope.doc")


def test_docx_passes_through(tmp_path) -> None:
    """已经是 docx 时直接复制到 out_dir。"""
    src = tmp_path / "src" / "a.docx"
    src.parent.mkdir()
    src.write_bytes(b"PK\x03\x04fake")
    out = tmp_path / "out"
    result = convert_doc_to_docx(src, out)
    assert result.exists()
    assert result.parent.resolve() == out.resolve()


@pytest.mark.integration
@pytest.mark.skipif(not HAS_SOFFICE, reason="未安装 LibreOffice")
def test_convert_real_doc(sample_doc_path: Path, tmp_path) -> None:
    """端到端：真实 .doc → .docx。"""
    out = convert_doc_to_docx(sample_doc_path, tmp_path)
    assert out.exists()
    assert out.suffix == ".docx"
    # 简单 sanity：docx 是 zip
    assert out.read_bytes()[:2] == b"PK"
