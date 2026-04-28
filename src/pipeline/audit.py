"""审核流水线：单文档 → 解析 → 切块 → 入库 → 多维度 agent → metrics → 输出。"""

from __future__ import annotations

import datetime as dt
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.agents import (
    AgentResult,
    BaseAgent,
    C1StructureAgent,
    C4ReferenceAgent,
    E1StaffingAgent,
)
from src.chunk import Chunk, chunk_docx_blocks
from src.config import get_default_config
from src.llm import LLMProvider, build_provider
from src.metrics import MetricsContext, compute_metrics
from src.parse import convert_doc_to_docx, parse_docx
from src.store import Repository


log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 维度路由：哪些 chunk 喂给哪些维度
# ---------------------------------------------------------------------------

# E1 人员配备：相关关键词
E1_KEYWORDS = (
    "员工", "工程师", "巡护", "巡线", "区段长", "管道", "总长",
    "管辖", "里程", "天然气", "成品油", "输油", "HSE",
)


def is_relevant_for_e1(chunk: Chunk) -> bool:
    text = chunk.content or ""
    return any(kw in text for kw in E1_KEYWORDS)


def assign_dimensions(chunks: list[Chunk]) -> None:
    """把维度标签写到 chunk.dimensions（in-place）。

    注意：C1 和 C4 是文档级维度（要看全部 chunk 决定结构/引用），
    不需要预路由；这里只为细粒度维度（如 E1）做预路由。
    """
    for c in chunks:
        if is_relevant_for_e1(c):
            if "E1_staffing" not in c.dimensions:
                c.dimensions.append("E1_staffing")


# ---------------------------------------------------------------------------
# 结果类
# ---------------------------------------------------------------------------


@dataclass
class AuditResult:
    doc_id: str
    doc_name: str
    review_timestamp: str
    dimensions: dict[str, dict[str, Any]]
    overall_verdict: str = "uncertain"
    overall_score: int = 0
    need_human_review: bool = False
    elapsed_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "doc_name": self.doc_name,
            "review_timestamp": self.review_timestamp,
            "dimensions": self.dimensions,
            "overall_verdict": self.overall_verdict,
            "overall_score": self.overall_score,
            "need_human_review": self.need_human_review,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
        }


def derive_doc_id(path: Path) -> str:
    stem = path.stem
    return re.sub(r"[^A-Za-z0-9_-]+", "_", stem)[:80]


# ---------------------------------------------------------------------------
# 顶层 pipeline
# ---------------------------------------------------------------------------


class AuditPipeline:
    """多维度审核 pipeline。

    维度分两类：
    1. **agents**：BaseAgent 子类。喂 chunks，输出 AgentResult。
    2. **metrics**：T1-T3，从运行上下文（耗时/格式）推导，不调 LLM。

    新增维度只需要：
    - 实现 BaseAgent 子类
    - 在 _build_agents() 里 register
    """

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        provider: LLMProvider | None = None,
        repo: Repository | None = None,
    ) -> None:
        self.config = config or get_default_config()
        self.provider = provider or build_provider(self.config)
        db_path = self.config.get("paths", {}).get("db_path", ":memory:")
        self.repo = repo or Repository(db_path)

        text_model = self.config.get("llm", {}).get("text_model", "Qwen3-2B")
        self.agents: list[BaseAgent] = self._build_agents(text_model)

    def _build_agents(self, text_model: str) -> list[BaseAgent]:
        return [
            C1StructureAgent(self.provider, text_model),
            C4ReferenceAgent(self.provider, text_model),
            E1StaffingAgent(self.provider, text_model),
        ]

    # ------------------------------------------------------------------
    def run(self, doc_path: str | Path) -> AuditResult:
        t0 = time.perf_counter()
        path = Path(doc_path).resolve()
        doc_id = derive_doc_id(path)
        log.info("Auditing %s as doc_id=%s", path.name, doc_id)

        # 1) 解析 + 切块 + 入库
        parse_ok = True
        try:
            chunks = self._ingest(path, doc_id)
        except Exception as e:
            log.error("Ingest failed for %s: %s", path.name, e)
            chunks = []
            parse_ok = False

        # 2) 跑所有 agent
        dim_results: dict[str, AgentResult] = {}
        if parse_ok and chunks:
            for agent in self.agents:
                try:
                    res = self._run_agent(agent, chunks)
                except Exception as e:
                    log.exception("Agent %s 执行失败", agent.dimension)
                    res = AgentResult(
                        dimension=agent.dimension,
                        verdict="uncertain",
                        confidence=10,
                        details=f"agent 执行异常：{e}",
                        need_human_review=True,
                    )
                dim_results[res.dimension] = res

        # 3) 跑 metrics（T1-T3）
        elapsed = time.perf_counter() - t0
        ctx = MetricsContext(
            doc_path=path,
            chunks=chunks,
            elapsed_seconds=elapsed,
            input_format=path.suffix.lower(),
            parse_succeeded=parse_ok,
        )
        for dim, res in compute_metrics(ctx).items():
            dim_results[dim] = res

        # 4) 持久化所有维度
        for res in dim_results.values():
            self._persist_label(doc_id, res)

        # 5) 汇总
        overall_verdict, overall_score = self._aggregate(dim_results)

        return AuditResult(
            doc_id=doc_id,
            doc_name=path.name,
            review_timestamp=dt.datetime.now().isoformat(),
            dimensions={k: v.to_dict() for k, v in dim_results.items()},
            overall_verdict=overall_verdict,
            overall_score=overall_score,
            need_human_review=any(r.need_human_review for r in dim_results.values()),
            elapsed_seconds=elapsed,
        )

    # ------------------------------------------------------------------
    def _ingest(self, path: Path, doc_id: str) -> list[Chunk]:
        cfg = self.config
        out_dir = Path(cfg["paths"]["data_dir"]) / "tmp" / doc_id
        out_dir.mkdir(parents=True, exist_ok=True)

        if path.suffix.lower() == ".doc":
            converted = convert_doc_to_docx(
                path, out_dir, timeout=cfg["parse"]["doc_to_docx_timeout"]
            )
            blocks_path = converted
        else:
            blocks_path = path

        if blocks_path.suffix.lower() in (".docx", ".docm"):
            image_dir = Path(cfg["paths"]["images_dir"]) / doc_id
            blocks = parse_docx(blocks_path, image_out_dir=image_dir)
        else:
            raise NotImplementedError(
                f"暂未实现 PDF 路径的 ingest: {blocks_path}"
            )

        chunks = chunk_docx_blocks(
            blocks,
            doc_id=doc_id,
            max_tokens=cfg["chunk"]["max_tokens"],
            table_inline_rows=cfg["chunk"]["table_inline_rows"],
        )
        assign_dimensions(chunks)
        self.repo.upsert_chunks(chunks)
        log.info("Stored %d chunks for %s", len(chunks), doc_id)
        return chunks

    # ------------------------------------------------------------------
    def _run_agent(self, agent: BaseAgent, chunks: list[Chunk]) -> AgentResult:
        """根据 agent 的维度路由相关 chunks。"""
        dim = agent.dimension

        # E1 用预路由的 dimensions 字段
        if dim == "E1_staffing":
            relevant = [c.to_row() for c in chunks if dim in c.dimensions]
            if not relevant:
                return AgentResult(
                    dimension=dim,
                    verdict="uncertain",
                    confidence=20,
                    details="未在文档中找到与人员配备相关的段落。",
                    need_human_review=True,
                )
            return agent.run(relevant)

        # C1 / C4 是文档级，喂全部 chunks
        rows = [c.to_row() for c in chunks]
        return agent.run(rows)

    # ------------------------------------------------------------------
    def _persist_label(self, doc_id: str, result: AgentResult) -> None:
        self.repo.upsert_label(
            label_id=f"{doc_id}__{result.dimension}__audit",
            doc_id=doc_id,
            dimension=result.dimension,
            pipeline="audit",
            final_verdict=result.verdict,
            score=result.score,
            confidence=result.confidence,
            findings=[f.to_dict() for f in result.findings],
            extra=result.extra,
            need_human_review=result.need_human_review,
            human_signoff=False,
        )

    # ------------------------------------------------------------------
    @staticmethod
    def _aggregate(dim_results: dict[str, AgentResult]) -> tuple[str, int]:
        """汇总 overall verdict 和总分。

        策略：
        - 任一维度 fail → overall fail
        - 任一维度 uncertain → overall uncertain（除非全是 metrics 维度的 fail）
        - 全部 pass → overall pass
        - 其余 → partial
        总分 = 各维度 score 之和（None 视为 0）
        """
        verdicts = [r.verdict for r in dim_results.values()]
        scores = [r.score or 0 for r in dim_results.values()]
        total = sum(scores)

        if not verdicts:
            return "uncertain", 0
        if "fail" in verdicts:
            return "fail", total
        if all(v == "pass" for v in verdicts):
            return "pass", total
        if "uncertain" in verdicts:
            return "uncertain", total
        return "partial", total

    def close(self) -> None:
        self.repo.close()


def audit_document(
    doc_path: str | Path,
    config: dict[str, Any] | None = None,
) -> AuditResult:
    pipe = AuditPipeline(config=config)
    try:
        return pipe.run(doc_path)
    finally:
        pipe.close()
