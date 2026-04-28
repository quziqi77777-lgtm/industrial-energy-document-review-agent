"""PDF 解析：pdfplumber 取文字、PyMuPDF (fitz) 取嵌入图片。

单页粒度即可——后续切块再按章节合并。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import fitz  # PyMuPDF
import pdfplumber


log = logging.getLogger(__name__)


@dataclass
class PdfPage:
    page_num: int          # 1-based
    text: str              # 该页全文
    images: list[Path] = field(default_factory=list)


def _extract_page_images(
    pdf_doc: fitz.Document,
    page_idx: int,
    out_dir: Path,
    counter_start: int,
) -> tuple[list[Path], int]:
    """从 PyMuPDF 页提取嵌入图片，返回 (paths, next_counter)。"""
    page = pdf_doc[page_idx]
    paths: list[Path] = []
    counter = counter_start
    for img_info in page.get_images(full=True):
        xref = img_info[0]
        try:
            pix = fitz.Pixmap(pdf_doc, xref)
            if pix.n - pix.alpha >= 4:  # CMYK -> RGB
                pix = fitz.Pixmap(fitz.csRGB, pix)
            counter += 1
            out_path = out_dir / f"page{page_idx + 1:03d}_img{counter:04d}.png"
            pix.save(str(out_path))
            paths.append(out_path)
            pix = None
        except Exception as e:
            log.warning("跳过 PDF 图 xref=%s: %s", xref, e)
    return paths, counter


def parse_pdf(
    path: str | Path,
    *,
    image_out_dir: str | Path | None = None,
) -> list[PdfPage]:
    """逐页解析 PDF，返回每页文字 + 图片路径。"""
    src = Path(path)
    if not src.exists():
        raise FileNotFoundError(f"PDF 不存在: {src}")
    img_dir = Path(image_out_dir) if image_out_dir else None
    if img_dir:
        img_dir.mkdir(parents=True, exist_ok=True)

    pages: list[PdfPage] = []
    img_counter = 0

    with pdfplumber.open(str(src)) as pdf, fitz.open(str(src)) as pdf_doc:
        for i, plumber_page in enumerate(pdf.pages):
            text = plumber_page.extract_text() or ""
            images: list[Path] = []
            if img_dir is not None:
                images, img_counter = _extract_page_images(
                    pdf_doc, i, img_dir, img_counter
                )
            pages.append(PdfPage(page_num=i + 1, text=text, images=images))

    log.info("Parsed %s: %d pages, %d images", src.name, len(pages), img_counter)
    return pages
