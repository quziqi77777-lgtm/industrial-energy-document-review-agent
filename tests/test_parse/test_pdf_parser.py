"""PDF 解析与扫描件检测测试。"""

from __future__ import annotations

from pathlib import Path

import pytest
import fitz

from src.parse.pdf_parser import parse_pdf
from src.parse.scan_detector import is_scanned_pdf


@pytest.fixture
def text_pdf(tmp_path: Path) -> Path:
    """构造一个有文字层的 PDF。"""
    p = tmp_path / "text.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text(
        (50, 80),
        "Hello world. 这是一个测试 PDF，含多行中文文字内容供 FTS 召回。" * 3,
        fontsize=11,
    )
    page2 = doc.new_page()
    page2.insert_text((50, 80), "第二页内容", fontsize=11)
    doc.save(str(p))
    doc.close()
    return p


def test_parse_pdf_pages(text_pdf: Path) -> None:
    pages = parse_pdf(text_pdf)
    assert len(pages) == 2
    assert "测试" in pages[0].text or "Hello" in pages[0].text


def test_missing_pdf_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        parse_pdf(tmp_path / "nope.pdf")


def test_text_pdf_not_scanned(text_pdf: Path) -> None:
    assert is_scanned_pdf(text_pdf) is False


@pytest.fixture
def scan_pdf(tmp_path: Path) -> Path:
    """构造一个无文字层的 PDF（仅图片）。"""
    p = tmp_path / "scan.pdf"
    doc = fitz.open()
    page = doc.new_page(width=400, height=400)
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 100, 100))
    pix.clear_with(0xff)
    page.insert_image(fitz.Rect(0, 0, 100, 100), pixmap=pix)
    doc.save(str(p))
    doc.close()
    return p


def test_scan_pdf_detected(scan_pdf: Path) -> None:
    assert is_scanned_pdf(scan_pdf) is True
