"""SQLite Repository：所有 CRUD 集中在此，业务代码不直接写 SQL。"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator

from src.chunk.models import Chunk


_SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def get_schema_sql() -> str:
    return _SCHEMA_PATH.read_text(encoding="utf-8")


class Repository:
    """SQLite 数据访问。

    线程安全策略：每个连接绑到调用线程，长任务自行 `with repo.connect()`。
    """

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        with self._conn:
            self._conn.executescript(get_schema_sql())

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self._conn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    # ---------- chunks ----------

    def upsert_chunks(self, chunks: Iterable[Chunk]) -> int:
        rows = [c.to_row() for c in chunks]
        if not rows:
            return 0
        cols = list(rows[0].keys())
        placeholders = ", ".join([f":{c}" for c in cols])
        sql = (
            f"INSERT OR REPLACE INTO chunks ({', '.join(cols)}) VALUES ({placeholders})"
        )
        fts_sql = (
            "INSERT INTO chunks_fts (chunk_id, doc_id, content, title) "
            "VALUES (:chunk_id, :doc_id, :content, :title)"
        )
        with self.connect() as conn:
            conn.executemany(sql, rows)
            # FTS：先删再插（INSERT OR REPLACE 不触发 FTS 删除）
            for r in rows:
                conn.execute(
                    "DELETE FROM chunks_fts WHERE chunk_id = ?", (r["chunk_id"],)
                )
            conn.executemany(fts_sql, rows)
        return len(rows)

    def get_chunk(self, chunk_id: str) -> dict | None:
        cur = self._conn.execute(
            "SELECT * FROM chunks WHERE chunk_id = ?", (chunk_id,)
        )
        row = cur.fetchone()
        return _row_to_dict(row) if row else None

    def get_chunks_by_doc(self, doc_id: str) -> list[dict]:
        cur = self._conn.execute(
            "SELECT * FROM chunks WHERE doc_id = ? ORDER BY paragraph_index",
            (doc_id,),
        )
        return [_row_to_dict(r) for r in cur.fetchall()]

    def get_chunks_by_dimension(self, doc_id: str, dimension: str) -> list[dict]:
        """按维度过滤；dimensions 字段是 JSON array，用 LIKE 模糊匹配。"""
        pattern = f'%"{dimension}"%'
        cur = self._conn.execute(
            "SELECT * FROM chunks WHERE doc_id = ? AND dimensions LIKE ? "
            "ORDER BY paragraph_index",
            (doc_id, pattern),
        )
        return [_row_to_dict(r) for r in cur.fetchall()]

    def search_chunks(
        self,
        query: str,
        doc_id: str | None = None,
        top_k: int = 10,
    ) -> list[dict]:
        """检索 chunks：trigram FTS5 优先，LIKE 兜底短查询。"""

        def _doc_clause() -> tuple[str, list[Any]]:
            return (" AND c.doc_id = ?", [doc_id]) if doc_id else ("", [])

        # 1. trigram FTS5
        tokens = [t for t in query.split() if len(t) >= 3]
        if not tokens and len(query.replace(" ", "")) >= 3:
            tokens = [query]
        if tokens:
            fts_query = " OR ".join(f'"{t}"' for t in tokens)
            doc_sql, doc_params = _doc_clause()
            sql = (
                "SELECT c.* FROM chunks_fts f JOIN chunks c ON c.chunk_id = f.chunk_id "
                "WHERE chunks_fts MATCH ?" + doc_sql + " LIMIT ?"
            )
            params: list[Any] = [fts_query, *doc_params, top_k]
            try:
                cur = self._conn.execute(sql, params)
                rows = [_row_to_dict(r) for r in cur.fetchall()]
                if rows:
                    return rows
            except sqlite3.OperationalError:
                pass

        # 2. LIKE 兜底
        doc_sql, doc_params = _doc_clause()
        sql = (
            "SELECT * FROM chunks WHERE (content LIKE ? OR title LIKE ?)"
            + doc_sql.replace("c.doc_id", "doc_id")
            + " LIMIT ?"
        )
        like_q = f"%{query}%"
        cur = self._conn.execute(sql, [like_q, like_q, *doc_params, top_k])
        return [_row_to_dict(r) for r in cur.fetchall()]

    # ---------- standards ----------

    def upsert_standard(
        self,
        clause_id: str,
        standard_name: str,
        clause_num: str,
        title: str,
        content: str,
        tags: list[str],
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO standards "
                "(id, standard_name, clause_num, title, content, tags) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    clause_id,
                    standard_name,
                    clause_num,
                    title,
                    content,
                    json.dumps(tags, ensure_ascii=False),
                ),
            )
            conn.execute("DELETE FROM standards_fts WHERE id = ?", (clause_id,))
            conn.execute(
                "INSERT INTO standards_fts (id, standard_name, title, content, tags) "
                "VALUES (?, ?, ?, ?, ?)",
                (clause_id, standard_name, title, content, " ".join(tags)),
            )

    def search_standards(
        self,
        query: str,
        standard_filter: list[str] | None = None,
        top_k: int = 3,
    ) -> list[dict]:
        """检索标准条款；trigram FTS5 命中率优先，LIKE 兜底短查询。"""
        params: list[Any]

        def _with_filter(sql: str, base_params: list[Any]) -> tuple[str, list[Any]]:
            if standard_filter:
                placeholders = ",".join(["?"] * len(standard_filter))
                sql += f" AND s.standard_name IN ({placeholders})"
                base_params.extend(standard_filter)
            sql += " LIMIT ?"
            base_params.append(top_k)
            return sql, base_params

        # 1. 先尝试 trigram FTS5（适合 ≥3 字符的查询；多 token 用 OR 拆分）
        fts_results: list[dict] = []
        # 提取 ≥3 字符的 token，用 OR 拼接
        tokens = [t for t in query.split() if len(t) >= 3]
        if not tokens and len(query.replace(" ", "")) >= 3:
            tokens = [query]
        if tokens:
            fts_query = " OR ".join(f'"{t}"' for t in tokens)
            sql = (
                "SELECT s.* FROM standards_fts f JOIN standards s ON s.id = f.id "
                "WHERE standards_fts MATCH ?"
            )
            params = [fts_query]
            sql, params = _with_filter(sql, params)
            try:
                cur = self._conn.execute(sql, params)
                fts_results = [_row_to_dict(r) for r in cur.fetchall()]
            except sqlite3.OperationalError:
                fts_results = []
        if fts_results:
            return fts_results

        # 2. LIKE 兜底（短查询或 FTS 未命中）
        sql = (
            "SELECT s.* FROM standards s "
            "WHERE (s.content LIKE ? OR s.title LIKE ? OR s.tags LIKE ?)"
        )
        like_q = f"%{query}%"
        params = [like_q, like_q, like_q]
        sql, params = _with_filter(sql, params)
        cur = self._conn.execute(sql, params)
        return [_row_to_dict(r) for r in cur.fetchall()]

    # ---------- labels ----------

    def upsert_label(
        self,
        *,
        label_id: str,
        doc_id: str,
        dimension: str,
        pipeline: str,
        final_verdict: str | None = None,
        score: int | None = None,
        confidence: int | None = None,
        explorer_a: dict | None = None,
        explorer_b: dict | None = None,
        critic: dict | None = None,
        findings: list[dict] | None = None,
        extra: dict | None = None,
        need_human_review: bool = False,
        human_signoff: bool = False,
    ) -> None:
        def _j(v: Any) -> str | None:
            return json.dumps(v, ensure_ascii=False) if v is not None else None

        with self.connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO labels (
                    label_id, doc_id, dimension, pipeline,
                    explorer_a, explorer_b, critic,
                    final_verdict, score, confidence,
                    findings, extra,
                    need_human_review, human_signoff
                ) VALUES (?,?,?,?, ?,?,?, ?,?,?, ?,?, ?,?)""",
                (
                    label_id,
                    doc_id,
                    dimension,
                    pipeline,
                    _j(explorer_a),
                    _j(explorer_b),
                    _j(critic),
                    final_verdict,
                    score,
                    confidence,
                    _j(findings),
                    _j(extra),
                    1 if need_human_review else 0,
                    1 if human_signoff else 0,
                ),
            )

    def get_labels(
        self,
        doc_id: str,
        pipeline: str | None = None,
    ) -> list[dict]:
        if pipeline:
            cur = self._conn.execute(
                "SELECT * FROM labels WHERE doc_id = ? AND pipeline = ?",
                (doc_id, pipeline),
            )
        else:
            cur = self._conn.execute(
                "SELECT * FROM labels WHERE doc_id = ?", (doc_id,)
            )
        return [_row_to_dict(r) for r in cur.fetchall()]


def _row_to_dict(row: sqlite3.Row | None) -> dict:
    if row is None:
        return {}
    out = dict(row)
    for k in ("dimensions", "cross_refs", "extra", "tags",
              "explorer_a", "explorer_b", "critic", "findings"):
        if k in out and isinstance(out[k], str) and out[k]:
            try:
                out[k] = json.loads(out[k])
            except json.JSONDecodeError:
                pass
    return out
