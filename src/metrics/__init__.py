"""指标模块：T1 模板使用 / T2 格式兼容 / T3 识别效率。

这些不是 agent（不需要 LLM 推理），而是 pipeline 跑完后从运行上下文里
读取的"运行时指标"。pipeline 在 `_persist_label` 之外，会再调用
`compute_metrics(...)` 把 T1-T3 写到一个独立的 metrics 维度里。
"""

from .compute import (
    MetricsContext,
    compute_metrics,
    compute_t1_template,
    compute_t2_format,
    compute_t3_latency,
)

__all__ = [
    "MetricsContext",
    "compute_metrics",
    "compute_t1_template",
    "compute_t2_format",
    "compute_t3_latency",
]
