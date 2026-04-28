"""Chunk 通用数据模型。

设计要点（与 system_design.md 第四节一致）：
- chunk_id 规范：`{doc_id}__{section_path}__{type}__{seq}`
- 层级路径 section_path：如 "5.1"、"APP_C"
- 稳定锚点：paragraph_index + anchor_text 用于定位批注
- 维度路由：每个 chunk 声明它与哪些维度相关（D/C/E/L 等）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ChunkType(str, Enum):
    TEXT = "text"
    TABLE_SUMMARY = "table_summary"
    TABLE_FULL = "table_full"
    IMAGE = "image"
    APPENDIX = "appendix"
    COVER = "cover"
    TOC = "toc"
    HEADING = "heading"


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    chunk_type: ChunkType
    section_path: str
    title: str
    content: str
    paragraph_index: int                 # 在原文档 XML 中的顺序
    anchor_text: str                     # 用于反查段落定位
    page_start: int | None = None        # 仅 PDF 才填
    page_end: int | None = None
    dimensions: list[str] = field(default_factory=list)
    cross_refs: list[str] = field(default_factory=list)
    word_count: int = 0
    parent_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_row(self) -> dict[str, Any]:
        """转换为可写入 SQLite 的 dict。"""
        import json

        return {
            "chunk_id": self.chunk_id,
            "doc_id": self.doc_id,
            "chunk_type": self.chunk_type.value,
            "section_path": self.section_path,
            "title": self.title,
            "content": self.content,
            "paragraph_index": self.paragraph_index,
            "anchor_text": self.anchor_text,
            "page_start": self.page_start,
            "page_end": self.page_end,
            "dimensions": json.dumps(self.dimensions, ensure_ascii=False),
            "cross_refs": json.dumps(self.cross_refs, ensure_ascii=False),
            "word_count": self.word_count,
            "parent_id": self.parent_id,
            "extra": json.dumps(self.extra, ensure_ascii=False),
        }
