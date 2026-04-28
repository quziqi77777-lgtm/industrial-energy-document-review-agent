"""审核结果 JSON 输出。

固定输出 schema：合并 sample_label_result.json 的字段约定与 system_design 第 10.2 节。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def write_audit_json(
    result: dict[str, Any],
    out_path: str | Path,
) -> Path:
    """把 AuditResult.to_dict() 写到 out_path（utf-8, ensure_ascii=False）。"""
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return out
