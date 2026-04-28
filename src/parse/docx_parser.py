"""DOCX 解析：把段落/标题/表格/嵌入图片按 XML 顺序展开为统一的 Block 流。

设计要点：
- 不转 Markdown：直接用 python-docx 读 `style.name`（Heading 1/2/3）比解析 `##` 准
- 每个 Block 携带 `paragraph_index`（XML 中的顺序号），用于稳定的批注定位
- `anchor_text`：取段落开头若干字符，作为 chunk 在原文中的唯一锚点
- 图片提取：仅记录关系（`relationship_id` + 落盘路径），具体图片处理由调用方决定
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Iterator

from docx import Document
from docx.document import Document as DocxDocument
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph


log = logging.getLogger(__name__)


class DocxBlockType(str, Enum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    TABLE = "table"
    IMAGE = "image"


@dataclass
class DocxBlock:
    """段落 / 标题 / 表格 / 图片的统一表示。"""

    block_type: DocxBlockType
    paragraph_index: int                 # 在文档中的顺序（XML 顺序，对所有 block 全局递增）
    text: str = ""                       # 段落原文 / 表格 markdown / 图片说明
    style: str | None = None             # 样式名（如 "Heading 1"）
    heading_level: int | None = None     # 1-6；非标题为 None
    anchor_text: str = ""                # 用于反查段落定位的唯一片段
    table_rows: list[list[str]] = field(default_factory=list)  # 仅 table
    image_rid: str | None = None         # 仅 image：嵌入关系 id
    image_path: Path | None = None       # 仅 image：落盘路径


_HEADING_RE = re.compile(r"^Heading\s+(\d+)$", re.IGNORECASE)


def _heading_level(style_name: str | None) -> int | None:
    if not style_name:
        return None
    m = _HEADING_RE.match(style_name.strip())
    return int(m.group(1)) if m else None


# 伪标题检测：很多工业 docx 不用 Word 的"标题样式"，而是用普通段落写编号
# 如"一、概述" / "1.1 目的" / "第一章 概况" / "（一）岗位条件"
_PSEUDO_HEADING_PATTERNS: list[tuple[re.Pattern, int]] = [
    # 注意：更具体的模式必须放前面（如 1.1 必须先于 1.）
    # 第一章 / 第二节
    (re.compile(r"^第\s*[一二三四五六七八九十百\d]+\s*[章节]"), 1),
    # 1.1.1（三级）
    (re.compile(r"^\d+\.\d+\.\d+\s*\S"), 3),
    # 1.1（二级）
    (re.compile(r"^\d+\.\d+(?!\d)\s*\S"), 2),
    # 一、 / 二、概述
    (re.compile(r"^[一二三四五六七八九十]+[、.．]\s*\S"), 1),
    # 1. / 1、 / 1．（一级，只能匹配单层数字后接非点的）
    (re.compile(r"^\d+\s*[、．]\s*\S"), 1),
    # 1.（一级，要求 . 后面不是数字，避免吞掉 1.1）
    (re.compile(r"^\d+\.(?!\d)\s*\S"), 1),
    # （一）/ (1) / （1）
    (re.compile(r"^[（(][一二三四五六七八九十\d]+[）)]\s*\S"), 3),
    # 附录 A / 附录一
    (re.compile(r"^附\s*录\s*[A-Z一二三四五六七八九十]"), 1),
]

# 长度上限：太长就不是标题（一般标题 ≤40 字）
_PSEUDO_HEADING_MAX_LEN = 40


def _detect_pseudo_heading(text: str) -> int | None:
    """根据文本模式判断段落是不是伪标题。返回 level 或 None。"""
    text = text.strip()
    if not text or len(text) > _PSEUDO_HEADING_MAX_LEN:
        return None
    for pat, level in _PSEUDO_HEADING_PATTERNS:
        if pat.match(text):
            return level
    return None


def _paragraph_text(p: Paragraph) -> str:
    """安全提取段落文本（合并所有 run）。"""
    return "".join(r.text for r in p.runs).strip() or p.text.strip()


def _make_anchor(text: str, max_len: int = 30) -> str:
    """前 N 个非空白字符作为锚点。"""
    cleaned = re.sub(r"\s+", "", text)
    return cleaned[:max_len]


def _table_to_rows(table: Table) -> list[list[str]]:
    rows: list[list[str]] = []
    for row in table.rows:
        cells = [cell.text.strip() for cell in row.cells]
        rows.append(cells)
    return rows


def _table_to_markdown(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    header = rows[0]
    md = ["| " + " | ".join(header) + " |", "|" + "|".join(["---"] * len(header)) + "|"]
    for r in rows[1:]:
        md.append("| " + " | ".join(r) + " |")
    return "\n".join(md)


def _iter_image_rids(p: Paragraph) -> Iterator[str]:
    """从段落 XML 中提取所有内嵌图片的 relationship id。"""
    for blip in p._element.iter(qn("a:blip")):
        rid = blip.get(qn("r:embed"))
        if rid:
            yield rid


def _extract_image(
    doc: DocxDocument,
    rid: str,
    out_dir: Path,
    counter: int,
) -> Path | None:
    """把 rid 对应的嵌入图片写到 out_dir，返回路径。"""
    try:
        rel = doc.part.related_parts[rid]
    except KeyError:
        log.warning("找不到关系 id=%s", rid)
        return None
    blob = rel.blob
    ext = ".png"
    if hasattr(rel, "content_type"):
        ct = rel.content_type
        if "jpeg" in ct or "jpg" in ct:
            ext = ".jpg"
        elif "gif" in ct:
            ext = ".gif"
        elif "wmf" in ct:
            ext = ".wmf"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"image_{counter:04d}{ext}"
    out_path.write_bytes(blob)
    return out_path


def parse_docx(
    path: str | Path,
    *,
    image_out_dir: str | Path | None = None,
) -> list[DocxBlock]:
    """解析一份 `.docx`，按 XML 顺序产出 Block 流。

    - `image_out_dir`：若提供，嵌入图片落到该目录；否则只记录 rid，不写盘。
    """
    src = Path(path)
    if not src.exists():
        raise FileNotFoundError(f"DOCX 不存在: {src}")
    doc: DocxDocument = Document(str(src))
    img_dir = Path(image_out_dir) if image_out_dir else None

    blocks: list[DocxBlock] = []
    pi = 0
    img_counter = 0

    body = doc.element.body
    for child in body.iterchildren():
        tag = child.tag.split("}")[-1]
        if tag == "p":
            p = Paragraph(child, doc.part)
            text = _paragraph_text(p)

            for rid in _iter_image_rids(p):
                img_path: Path | None = None
                if img_dir is not None:
                    img_counter += 1
                    img_path = _extract_image(doc, rid, img_dir, img_counter)
                blocks.append(
                    DocxBlock(
                        block_type=DocxBlockType.IMAGE,
                        paragraph_index=pi,
                        text=f"[image rid={rid}]",
                        style=p.style.name if p.style else None,
                        anchor_text=f"image_{rid}",
                        image_rid=rid,
                        image_path=img_path,
                    )
                )
                pi += 1

            if not text:
                continue

            style = p.style.name if p.style else None
            level = _heading_level(style)
            if level is None:
                # 工业文档常用普通段落写编号标题，做一次正则兜底
                level = _detect_pseudo_heading(text)
            block_type = DocxBlockType.HEADING if level else DocxBlockType.PARAGRAPH
            blocks.append(
                DocxBlock(
                    block_type=block_type,
                    paragraph_index=pi,
                    text=text,
                    style=style,
                    heading_level=level,
                    anchor_text=_make_anchor(text),
                )
            )
            pi += 1

        elif tag == "tbl":
            table = Table(child, doc.part)
            rows = _table_to_rows(table)
            md = _table_to_markdown(rows)
            anchor_src = " ".join(rows[0]) if rows else f"table_{pi}"
            blocks.append(
                DocxBlock(
                    block_type=DocxBlockType.TABLE,
                    paragraph_index=pi,
                    text=md,
                    anchor_text=_make_anchor(anchor_src),
                    table_rows=rows,
                )
            )
            pi += 1

    log.info(
        "Parsed %s: %d blocks (%d images extracted)",
        src.name,
        len(blocks),
        img_counter,
    )
    return blocks
