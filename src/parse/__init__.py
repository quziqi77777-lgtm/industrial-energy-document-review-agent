"""文档解析层：将异构原始文档转换为统一的段落/表格/图片流。"""

from .doc_converter import DocConversionError, convert_doc_to_docx
from .docx_parser import DocxBlock, DocxBlockType, parse_docx
from .pdf_parser import PdfPage, parse_pdf
from .scan_detector import is_scanned_pdf

__all__ = [
    "DocConversionError",
    "convert_doc_to_docx",
    "DocxBlock",
    "DocxBlockType",
    "parse_docx",
    "PdfPage",
    "parse_pdf",
    "is_scanned_pdf",
]
