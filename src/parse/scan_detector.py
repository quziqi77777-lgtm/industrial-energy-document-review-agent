"""判断 PDF 是否扫描件（无文字层）。

策略：抽样前 N 页，若每页文字字符数都低于阈值，视为扫描件，需走 OCR 兜底。
"""

from __future__ import annotations

from pathlib import Path

import pdfplumber


def is_scanned_pdf(
    path: str | Path,
    *,
    sample_pages: int = 3,
    min_chars_per_page: int = 50,
) -> bool:
    src = Path(path)
    with pdfplumber.open(str(src)) as pdf:
        n = min(sample_pages, len(pdf.pages))
        if n == 0:
            return False
        for i in range(n):
            text = pdf.pages[i].extract_text() or ""
            if len(text.strip()) >= min_chars_per_page:
                return False
    return True
