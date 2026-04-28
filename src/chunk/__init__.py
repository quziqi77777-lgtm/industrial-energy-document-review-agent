"""切块层：将段落/表格/图片流组织成 chunks（带层级路径与稳定 id）。"""

from .models import Chunk, ChunkType
from .text_chunk import chunk_docx_blocks

__all__ = ["Chunk", "ChunkType", "chunk_docx_blocks"]
