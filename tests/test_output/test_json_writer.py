"""JSON 输出测试。"""

from __future__ import annotations

import json
from pathlib import Path

from src.output import write_audit_json


def test_write_audit_json(tmp_path: Path) -> None:
    payload = {
        "doc_id": "TEST",
        "doc_name": "x.docx",
        "review_timestamp": "2026-04-28T10:00:00",
        "dimensions": {
            "E1_staffing": {
                "verdict": "partial",
                "score": 14,
                "extra": {
                    "staffing_analysis": {
                        "total_employees": 34,
                        "中文键": "也支持",
                    }
                },
            }
        },
        "overall_verdict": "partial",
        "overall_score": 14,
        "need_human_review": False,
    }
    out = tmp_path / "result.json"
    written = write_audit_json(payload, out)
    assert written.exists()
    loaded = json.loads(written.read_text(encoding="utf-8"))
    assert loaded["dimensions"]["E1_staffing"]["extra"]["staffing_analysis"]["中文键"] == "也支持"


def test_creates_parent_dirs(tmp_path: Path) -> None:
    nested = tmp_path / "a" / "b" / "c" / "result.json"
    write_audit_json({"k": "v"}, nested)
    assert nested.exists()
