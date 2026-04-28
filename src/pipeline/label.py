"""打标流水线：与 audit 共用底座，写入 labels 表（pipeline='label'，待人工签字）。

赛题数据集需要选手自标 GT；此 pipeline 输出"AI 预审"作为初稿，人工修改后将
`human_signoff` 标为 True 表示进入 GT。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.pipeline.audit import AuditPipeline, AuditResult


class LabelPipeline(AuditPipeline):
    """与 audit pipeline 同流程，仅在持久化时把 pipeline 字段改为 "label"。"""

    def _persist_label(self, doc_id: str, result) -> None:  # type: ignore[override]
        self.repo.upsert_label(
            label_id=f"{doc_id}__{result.dimension}__label",
            doc_id=doc_id,
            dimension=result.dimension,
            pipeline="label",
            final_verdict=result.verdict,
            score=result.score,
            confidence=result.confidence,
            findings=[f.to_dict() for f in result.findings],
            extra=result.extra,
            need_human_review=result.need_human_review,
            human_signoff=False,   # 默认未签字；GT 需要人工把它改成 True
        )


def label_document(
    doc_path: str | Path,
    config: dict[str, Any] | None = None,
) -> AuditResult:
    pipe = LabelPipeline(config=config)
    try:
        return pipe.run(doc_path)
    finally:
        pipe.close()
