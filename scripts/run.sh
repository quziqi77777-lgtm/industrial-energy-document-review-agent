#!/usr/bin/env bash
# Industry Agent 一键脚本
# 用法：
#   bash scripts/run.sh test                         # 跑全部测试（跳过需要 LibreOffice 的 integration）
#   bash scripts/run.sh audit <doc>                  # 用 mock 跑审核
#   bash scripts/run.sh audit-llm <doc>              # 用 vLLM 跑审核（先确保起好 vllm serve）
#   bash scripts/run.sh label <doc>                  # 用 mock 跑打标
#   bash scripts/run.sh vllm                         # 起 vLLM 文本模型 server（前台运行）

set -euo pipefail

# ---- 激活 conda env ----
source /data/work/ZiqiQu/miniconda3/etc/profile.d/conda.sh
conda activate industryAgent

cd "$(dirname "$0")/.."

case "${1:-}" in
  test)
    pytest tests/ -q --deselect tests/test_parse/test_doc_converter.py::test_convert_real_doc
    ;;
  audit)
    shift
    python -m src.cli audit "$@" --config config/mock.yaml -v
    ;;
  audit-llm)
    shift
    python -m src.cli audit "$@" -v
    ;;
  label)
    shift
    python -m src.cli label "$@" --config config/mock.yaml -v
    ;;
  vllm)
    # 文本模型，默认占用 GPU 0
    CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-3}" \
    vllm serve /data/work/ZiqiQu/models/ms_cache/Qwen/Qwen3-1___7B-Instruct \
      --served-model-name Qwen3-1.7B-Instruct \
      --port 8000 \
      --max-model-len 8192 \
      --gpu-memory-utilization 0.5
    ;;
  vllm-vl)
    # 视觉模型，默认占用 GPU 4
    CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-4}" \
    vllm serve /data/work/ZiqiQu/models/ms_cache/Qwen/Qwen3-VL-4B-Instruct \
      --served-model-name Qwen3-VL-4B-Instruct \
      --port 8001 \
      --max-model-len 8192 \
      --gpu-memory-utilization 0.5
    ;;
  *)
    cat <<EOF
用法:
  bash scripts/run.sh test                          跑测试
  bash scripts/run.sh audit  <docx>                 mock 模式审核
  bash scripts/run.sh label  <docx>                 mock 模式打标
  bash scripts/run.sh audit-llm <docx>              连真实 vLLM 审核
  bash scripts/run.sh vllm                          起 Qwen3-1.7B 文本服务（端口 8000）
  bash scripts/run.sh vllm-vl                       起 Qwen3-VL-4B 视觉服务（端口 8001）

提示：
- 仅支持 .docx；.doc 请先在 Word/WPS 里另存为 .docx
- 跑 audit-llm 前先开第二个终端跑 vllm 命令
EOF
    ;;
esac
