"""命令行入口。

用法：
    python -m src.cli audit <doc_path> [--out result.json] [--config config/default.yaml]
    python -m src.cli label <doc_path> [--out result.json]   # 写到 labels.pipeline=label
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from src.config import get_default_config, load_config
from src.output import write_audit_json
from src.pipeline.audit import AuditPipeline
from src.pipeline.label import LabelPipeline


log = logging.getLogger("industry_agent")


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )


def _build_pipeline(action: str, config_path: str | None):
    cfg = load_config(config_path) if config_path else get_default_config()
    if action == "audit":
        return AuditPipeline(cfg), cfg
    if action == "label":
        return LabelPipeline(cfg), cfg
    raise ValueError(f"未知动作: {action}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="industry-agent")
    parser.add_argument("action", choices=["audit", "label"], help="审核或打标")
    parser.add_argument("doc_path", help="待处理文档路径（.doc/.docx）")
    parser.add_argument("--out", help="结果 JSON 输出路径")
    parser.add_argument("--config", help="自定义配置 YAML")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    _setup_logging(args.verbose)
    doc_path = Path(args.doc_path).resolve()
    if not doc_path.exists():
        log.error("文件不存在: %s", doc_path)
        return 2

    pipe, cfg = _build_pipeline(args.action, args.config)
    try:
        result = pipe.run(doc_path)
    finally:
        pipe.close()

    out_path = Path(args.out) if args.out else (
        Path(cfg["paths"]["results_dir"]) / f"{result.doc_id}_{args.action}.json"
    )
    write_audit_json(result.to_dict(), out_path)
    log.info("结果已写入 %s", out_path)
    print(f"[{args.action}] {result.doc_name}")
    print(f"  doc_id: {result.doc_id}")
    print(f"  overall_verdict: {result.overall_verdict}")
    print(f"  overall_score:   {result.overall_score}")
    if result.need_human_review:
        print("  ⚠ 需要人工复核")
    print(f"  output: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
