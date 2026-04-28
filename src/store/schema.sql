-- IndustryAgent SQLite schema
-- 与 system_design.md 第 9.2 节一致；新增 paragraph_index/anchor_text 用于批注定位

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id         TEXT PRIMARY KEY,
    doc_id           TEXT NOT NULL,
    chunk_type       TEXT NOT NULL,
    section_path     TEXT,
    title            TEXT,
    content          TEXT,
    paragraph_index  INTEGER,
    anchor_text      TEXT,
    page_start       INTEGER,
    page_end         INTEGER,
    dimensions       TEXT,           -- JSON array
    cross_refs       TEXT,           -- JSON array
    word_count       INTEGER,
    parent_id        TEXT,
    extra            TEXT,           -- JSON object
    created_at       TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id);
CREATE INDEX IF NOT EXISTS idx_chunks_section ON chunks(doc_id, section_path);
CREATE INDEX IF NOT EXISTS idx_chunks_type ON chunks(chunk_type);

-- chunks 全文检索（content 字段）
-- 中文：trigram 分词器，支持 ≥3 字符的子串匹配；短查询用 repository 的 LIKE 兜底
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    chunk_id UNINDEXED,
    doc_id UNINDEXED,
    content,
    title,
    tokenize='trigram'
);

-- 国标/企标条款（按维度按关键词检索）
CREATE TABLE IF NOT EXISTS standards (
    id            TEXT PRIMARY KEY,    -- "TSG31-2025_3.2"
    standard_name TEXT NOT NULL,        -- "TSG31-2025"
    clause_num    TEXT,                 -- "3.2"
    title         TEXT,
    content       TEXT,
    tags          TEXT                 -- JSON array：["E1","管道分级"]
);

CREATE INDEX IF NOT EXISTS idx_standards_name ON standards(standard_name);

CREATE VIRTUAL TABLE IF NOT EXISTS standards_fts USING fts5(
    id UNINDEXED,
    standard_name UNINDEXED,
    title,
    content,
    tags,
    tokenize='trigram'
);

-- 图片块（场景二）
CREATE TABLE IF NOT EXISTS image_chunks (
    chunk_id        TEXT PRIMARY KEY,
    doc_id          TEXT NOT NULL,
    image_type      TEXT,              -- evacuation_route/entry_route/...
    image_path      TEXT,
    parent_chunk_id TEXT,
    description     TEXT,
    analysis        TEXT,              -- JSON
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_image_doc ON image_chunks(doc_id);
CREATE INDEX IF NOT EXISTS idx_image_type ON image_chunks(image_type);

-- 打标 / 审核结果（双流水线写入同一张表，靠 pipeline 字段区分）
CREATE TABLE IF NOT EXISTS labels (
    label_id          TEXT PRIMARY KEY,
    doc_id            TEXT NOT NULL,
    dimension         TEXT NOT NULL,
    pipeline          TEXT NOT NULL,        -- "audit" / "label"
    explorer_a        TEXT,                 -- JSON
    explorer_b        TEXT,                 -- JSON
    critic            TEXT,                 -- JSON
    final_verdict     TEXT,                 -- pass/partial/fail/uncertain
    score             INTEGER,
    confidence        INTEGER,
    findings          TEXT,                 -- JSON array
    extra             TEXT,                 -- JSON object（维度专属字段：staffing_analysis 等）
    need_human_review INTEGER DEFAULT 0,
    human_signoff     INTEGER DEFAULT 0,    -- GT 必须人工签字
    created_at        TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_labels_doc ON labels(doc_id);
CREATE INDEX IF NOT EXISTS idx_labels_dim ON labels(doc_id, dimension);

-- 批量任务
CREATE TABLE IF NOT EXISTS batch_jobs (
    doc_id      TEXT PRIMARY KEY,
    doc_path    TEXT,
    status      TEXT,
    started_at  TEXT,
    finished_at TEXT,
    error_msg   TEXT
);
