"""检索层：FTS5 关键词检索 + 维度标准库路由。"""

from .fts_search import search_standards_for_dimension, tokenize_for_fts

__all__ = ["search_standards_for_dimension", "tokenize_for_fts"]
