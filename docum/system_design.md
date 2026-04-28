# AI 审核系统设计文档

> 覆盖场景一（作业指导书文本审核）+ 场景二（高后果区风险管控方案多模态审核）
> 参考标准：Q/SY 1217-2009 / GB/T 1.1-2020 / TSG 31-2025 / GB/T 21246-2020 / AQ 3057-2025

---

## 〇、v2 修订说明（2026-04-28）

针对设计评审做的关键修订，详见对应章节：

1. **维度体系**：按赛题评分表对齐（场景一 C1-C5 + E1-E2 + T1-T3，场景二 I1-I8 + L1-L6 + T1-T3），原 D0-D8 编号在文档其余部分按映射理解；详见第六节。
2. **维度调用粒度（折中方案）**：
   - 场景一：C1 结构走规则引擎（不调 LLM）；C2-C5 合并 1 次 LLM 调用一次性输出 4 项 verdict；E1/E2 各自独立调用；T1/T2/T3 走规则。
   - 场景二：每张图按 `image_type` 分桶，一次 VL 调用产出该图所有相关检查项；上下文一致性校验（L1-L6）以规则函数为主，LLM 兜底。
3. **模型可插拔（先不做延迟硬约束）**：抽象 `LLMProvider` 接口，运行时通过配置切换 vLLM 本地 / OpenAI-compatible API；单份 ≤240 秒为软目标，先保正确率再调优延迟。
4. **检索策略**：保持原 SQLite FTS5 关键词检索为主路径（≈ "建索引版 grep"），后续如召回不足再叠加本地 embedding 兜底。
5. **测试要求**：每个 agent 模块在 `tests/` 下有对应单元/集成测试，详见第十三节。

---

## 一、整体架构

### 1.1 两个场景，共用一套底层

```
场景一：作业指导书（文本为主）      场景二：高后果区风险管控方案（文本+图片）
        │                                       │
        └──────────────┬────────────────────────┘
                       │  共用
              ┌────────┴────────┐
              │  文档解析层      │  python-docx / pdfplumber / PaddleOCR
              │  切块层          │  TextChunk + ImageChunk
              │  检索层          │  SQLite FTS5 + 按维度加载标准
              │  2+1 Agent层    │  Explorer A/B + Critic
              │  输出层          │  JSON + MD报告 + 带批注DOCX
              └─────────────────┘
```

### 1.2 两个阶段，共用同一套 Chunk

```
阶段一：打标     → 创建 ground truth，用于计算 accuracy
阶段二：审核系统 → 最终交付物，输出报告 + 带批注文档

关键原则：文档切块一次，两个阶段直接复用，不重复解析。
```

---

## 二、技术栈

| 层次 | 工具 | 说明 |
|------|------|------|
| 文档解析 | `python-docx` | WORD文件，直接读段落+样式，不转MD |
| 文档解析 | `pdfplumber` | 可编辑PDF文字提取 |
| 文档解析 | `PyMuPDF (fitz)` | PDF图片提取 |
| 扫描件兜底 | `PaddleOCR`（本地免费） | 仅整本扫描件PDF使用，其他情况不用 |
| 图片理解 | 通过 `LLMProvider.call_vision`，默认 `Qwen2.5-VL-72B`（本地） | 场景二图片分析、手写识别，一步替代 OCR+理解；可切 7B 加速 |
| 文本审核 Explorer A/B | 通过 `LLMProvider` 抽象，默认 `Qwen2.5-72B-Instruct`（vLLM 本地） | 128K 窗口；可切 32B/14B；接口预留 OpenAI-compatible，模型可随时替换 |
| Critic 仲裁 | 通过 `LLMProvider` 抽象，默认 `QwQ-32B` 或 `DeepSeek-R1-Distill-Llama-70B` | 推理强；可切更大模型 |
| 存储 | `SQLite` | 单文件，零部署，Python内置，FTS5全文检索 |
| 标准解析 | 已有 `MinerU_markdown_*.md` | 直接复用，不重新解析 |
| 输出批注 | `python-docx` Comment API | 写入带颜色批注的DOCX |

**不需要**：向量库、Embedding、RAG pipeline、独立OCR服务

---

## 三、文档解析策略

### 3.1 按格式分路

```python
def parse_document(path: Path):
    if path.suffix in ('.doc', '.docx'):
        return parse_docx(path)          # python-docx，直接拿段落+样式
    elif is_scanned_pdf(path):           # 页面全是图片，无文字层
        return parse_with_paddleocr(path) # PaddleOCR整体转文字，比逐页调多模态便宜10倍
    else:
        return parse_pdf(path)           # pdfplumber提取文字 + PyMuPDF提取图片
```

**WORD不转Markdown**：`python-docx`的`style.name`（Heading 1/2/3）比解析`##`符号更准确。

### 3.2 图片与OCR策略

```
需要识别的内容          处理方式
──────────────────────────────────────────────────
嵌入图片/示意图          Qwen2-VL → 结构化JSON（一步完成）
手写签名/日期            Qwen2-VL → 文字+置信度（一步完成，比Tesseract准）
表格内图片文字标注        Qwen2-VL → image_description存入ImageChunk
整本扫描件PDF            PaddleOCR整体转文字 → 再走文本流程
```

**原则**：多模态LLM一步完成识别+理解+结构化，不需要单独OCR步骤。

---

## 四、切块设计

### 4.1 切块原则

**按层级切，不按页切**。页码边界是印刷产物，不是语义边界。
"5.1 天然气泄漏处置"可能跨第203-218页，按页切会丢失标题上下文。

```
主切点：Heading 1/2/3（文档自身的语义结构）
次切点：超过 800 tokens 时按段落边界再切，子块继承父级 section_path
```

### 4.2 表格处理

```
≤10行的表    → 保留在父 section chunk 里，has_inline_table=True
>10行的表    → 切为独立 TABLE chunk，父块用占位符替代：
               "[TABLE: ASZYQ__5.1__table__001, 共47行，字段：序号/责任人/操作步骤]"
               同时生成：
               ├── TABLE_SUMMARY（表头+前3行+"共N行"）← Agent默认读这个
               └── TABLE_FULL（完整内容）← 按 chunk_id 精确取
```

### 4.3 图片处理

**图片永远独立成块**，父 chunk 用占位符引用：

```
"[IMAGE: ASZYQ__3.2__img__001, 描述: 管道阀室平面布置图，含标注：1号截断阀位置（桩号K230+500）]"
```

表格内的图片挂载到 TABLE chunk 下，不挂父 section。

### 4.4 chunk_id 规范

```
格式：{doc_id}__{section_path}__{type}__{seq}

示例：
ASZYQ__5.1__text__001        ← 5.1节第1段文字
ASZYQ__5.1__table__001       ← 5.1节第1个表（summary版）
ASZYQ__5.1__table__001__full ← 同表完整版
ASZYQ__5.1__img__002         ← 5.1节第2张图
ASZYQ__APP_C__text__001      ← 附录C正文
ASZYQ__COVER__approval__001  ← 封面签字页
```

### 4.5 TextChunk 数据结构

```python
@dataclass
class TextChunk:
    chunk_id:       str         # "ASZYQ__5.1__text__001"
    doc_id:         str
    chunk_type:     str         # text/table_summary/table_full/appendix/cover/toc
    section_path:   str         # "5.1"
    title:          str
    content:        str         # 纯文字，图片/长表用占位符
    page_start:     int
    page_end:       int

    # 路由（打标和审核共用）
    relevant_dimensions: list[str]  # ["D7","D4"]
    cross_refs_to:       list[str]  # ["ASZYQ__APP_C__text__001"]
    cross_refs_from:     list[str]  # 哪些块引用了本块

    # 关系
    parent_id:      str | None
    children_ids:   list[str]
    prev_sibling:   str | None
    next_sibling:   str | None

    # 特殊提取
    extracted_standards: list[str]  # ["TSG31-2025","GBT21246-2020"]
    extracted_numbers:   dict       # {"total_employees":120, "pipeline_km":85}
    has_images:          bool
    image_chunk_ids:     list[str]
    word_count:          int
    is_appendix:         bool
    appendix_id:         str | None  # "C"
```

### 4.6 ImageChunk 数据结构（场景二）

```python
@dataclass
class ImageChunk:
    chunk_id:       str         # "HCQA__2.3__img__001"
    doc_id:         str
    image_type:     str         # evacuation_route / entry_route / approval
                                # assembly_point / hca_aerial / material_diagram
    image_path:     str         # 图片文件路径
    parent_chunk_id: str
    description:    str         # 多模态LLM生成的自然语言描述（可FTS5检索）
    analysis:       dict        # 按image_type的结构化分析结果（见第八节）
```

### 4.7 文档切块树示意（场景一）

```
AS作业指导书.doc
├── [cover]       封面页                    → D8
├── [approval]    签字审批页（三栏）         → D8
├── [toc]         目录（含全部页码）         → D1
├── [chapter] 1.  岗位任职条件              → D1, D2
│   ├── [subsection] 1.1 学历要求
│   └── [table]      任职条件对照表
├── [chapter] 2.  岗位职责                  → D1, D2, D6（含人数数字）
├── [chapter] 3.  操作规程                  → D1, D2, D3（含压力/温度参数）
├── [chapter] 4.  巡回检查                  → D1
├── [chapter] 5.  应急处置                  → D1, D7, D4
│   ├── [subsection] 5.1 天然气泄漏处置
│   ├── [subsection] 5.2 火灾爆炸处置
│   └── [table]      应急步骤表（>10行→独立TABLE块）
├── [standards_block]                        → D2, D4（全文引用标准汇总）
└── [appendix]    附录A~T（20个）            → D4, D7
    ├── 附录A: HSE清单       cross_refs_from=["ASZYQ__3.2__text__001"]
    └── 附录C: 应急处置卡    cross_refs_from=["ASZYQ__5.1__text__001"]
```

---

## 五、检索策略

### 5.1 三类知识源，三种访问方式

| 知识源 | 大小 | 访问方式 |
|--------|------|---------|
| 赛题规则 + QSY1217（2K tokens） | 极小 | 直接进 System Prompt，不检索 |
| 国标/企标条款（按维度取1-2个文件） | 中等 | 按维度映射加载，不用检索 |
| 作业书 chunks | 已路由 | 按 `relevant_dimensions` 直接取，不用检索 |
| 附录 | 已索引 | 按 chunk_id 精确取 + cross_ref_map |

**不需要向量库，不需要 Embedding，不需要 BM25**。

### 5.2 标准文件的 Context 预算

各标准文件 token 估算：

```
TSG 31-2025      → ~48K tokens（最大）
GB/T 1.1-2020    → ~35K tokens
AQ 3057-2025     → ~25K tokens
GB/T 21246-2020  → ~13K tokens
QSY 1217-2009    → ~2K tokens（直接进Prompt）
```

DeepSeek-V3 / Qwen-turbo 窗口有限，**按维度只加载需要的标准**：

```python
DIMENSION_STANDARDS = {
    "D1": ["QSY1217", "GBT1.1"],           # 2K + 35K = 37K
    "D2": ["TSG31", "GBT21246", "QSY1217"], # 48K + 13K + 2K = 63K
    "D3": ["GBT1.1"],                       # 35K
    "D4": ["QSY1217", "GBT1.1"],           # 37K
    "D5": ["GBT1.1"],                       # 35K
    "D6": ["QSY1217"],                      # 2K（只需要这个）
    "D7": ["QSY1217", "AQ3057"],           # 2K + 25K = 27K
    "D8": ["GBT1.1"],                       # 35K
}
```

单次 Agent 调用的 Context 构成（以 Qwen-turbo 8K 为例）：
```
System Prompt + 赛题规则要点    →  800 tokens
作业书相关 chunks（1-2个）      → 1500 tokens
标准条款（按维度选，关键段落）   → 1500 tokens
输出 JSON                      → 2000 tokens
───────────────────────────────────────────
合计                            ~ 5800 tokens ✓
```

### 5.3 运行时 Context 构建

```python
def build_agent_context(doc_id: str, dimension: str) -> dict:
    return {
        "doc_chunks":  get_chunks_by_dimension(doc_id, dimension),  # SQL查询
        "appendices":  get_linked_appendices(doc_id, dimension),     # cross_ref_map
        "standards":   [load_standard_key_sections(s)               # 按维度加载
                        for s in DIMENSION_STANDARDS[dimension]],
        "rules":       COMPETITION_RULES,                            # 固定，永远在
    }
```

### 5.4 SQLite FTS5 替代文本搜索

需要在条款内容里做关键词搜索时（如找"阴极保护"相关条款）：

```python
# 国标全文检索（替代 grep）
cur.execute("SELECT * FROM standards_fts WHERE standards_fts MATCH ?", ("阴极保护",))

# 找某文档所有 D7 相关块
cur.execute("SELECT * FROM chunks WHERE doc_id=? AND dimensions LIKE '%D7%'", (doc_id,))
```

---

## 六、打标 Agent 架构：2+1

### 6.1 设计哲学

参考 ultrareview 的 3 Explorer + 1 Critic 架构移植到本场景：
两个 Explorer 互补分工（**互不可见对方输出，并行运行**），Critic 串行验证。

```
文档 chunks + 标准 + 赛题规则
           │
    ┌──────┴──────┐
    ▼             ▼
Explorer A    Explorer B     ← 并行，独立上下文
（temp=0.2）  （temp=0）
    │             │
    └──────┬──────┘
           ▼
       Critic Agent          ← 串行，拿到 A+B 全部输出
           │
           ▼
      ground truth label → 存入 SQLite labels 表
```

### 6.2 三个角色

#### Explorer A — 证据猎手
**偏见：宁可 uncertain，不伪造证据。防漏报。**

- 先穷举找所有相关原文片段，再基于证据判断
- 找不到足够证据 → 主动报 `uncertain`，附"找到了什么，没找到什么"
- temperature: 0.2

```
核心指令：
"你的首要任务是找证据，不是下结论。先列出文档中全部相关原文片段，
再基于这些片段判断。若原文支撑不足，verdict 必须为 uncertain。"
```

#### Explorer B — 规则对照员
**偏见：没明确满足就是 fail。防误放。**

- 拿 V3 规范逐条机械比对，不做推断
- 要求未被明确满足 → fail 或 partial
- temperature: 0

```
核心指令：
"你是规范的机械执行者。每条检查项必须找到原文明确满足的证据才能通过，
否则判 fail/partial。不允许基于推断给出 pass。"
```

#### Critic — 仲裁官
**职责：主动挑战两个 Explorer，不是简单投票。**

执行四项核查：
```
① 证据真实性验证
   A/B 引用的 evidence 是否逐字出现在原文？
   → 不在原文 → uncertain_evidence=True，强制人工复核

② 分歧裁决（A ≠ B 时）
   → "A 的证据是否充分？B 的规则理解是否正确？"
   → 给出有推理链的最终 verdict，不取平均

③ 跨维度矛盾检查
   → D1 pass ↔ D4 存在悬空引用？矛盾 → 降级置信度
   → D2 标准有效 ↔ D5 时间链错误？矛盾 → 人工复核

④ 遗漏补充
   → A/B 都未发现但 Critic 从证据中推断的隐性问题
   → critic_only_finding，置信度上限 60
```

### 6.3 按维度分配：客观维度跳过 Explorer A

| 维度 | 需要 Explorer A | 原因 |
|------|----------------|------|
| D2 标准版本 | 否 | 废止/有效有客观答案 |
| D6 人员配备 | 否 | 公式计算，结果唯一 |
| D8 封面要素 | 否 | 字段存在与否可直接判断 |
| D1 结构完整性 | **是** | 模块覆盖有边界模糊 |
| D3 语言规范性 | **是** | 缩略词注释率等主观判断 |
| D4 文件引用 | **是** | 附录-正文交叉验证 |
| D5 业务逻辑 | **是** | 跨章节数据一致性 |
| D7 应急处置 | **是** | 覆盖矩阵+处置顺序双重判断 |

### 6.4 Critic 分歧裁决规则

```
A=uncertain, B=fail   → 优先信 B，除非 B 的 evidence 验证失败
A=partial,  B=fail    → 看 B 的规则条款是否被 A 的证据间接满足
A=pass,     B=partial → 看 A 的证据是否真的覆盖了 B 指出的缺失项
A=pass,     B=fail    → 高分歧，Critic 必须写 ≥100 字裁决理由，置信度上限 75
A=B + 证据有效        → 直接采用，confidence = (A.conf + B.conf) / 2
```

### 6.5 置信度分级

```
高置信（自动入库）：A=B + 证据验证通过 + 无跨维度矛盾，confidence ≥ 80
中置信（加标记入库）：A≠B 但 Critic 给出裁决理由，confidence 50-79
低置信（人工裁定）：证据验证失败 / 矛盾未解 / confidence < 50
```

触发人工复核：
- 任何维度 `verdict = "uncertain"`
- `uncertain_evidence = True`（Critic 验证失败）
- 跨维度矛盾未解
- D2 标准废止风险 / D6 `rules_passed ≤ 2` / D7 `coverage_rate < 0.4`
- `overall_confidence < 60`
- 存在 `critic_only_finding`

---

## 七、审核系统（阶段二）

### 7.1 与打标复用同一套 Chunk

审核系统直接复用切块、路由、Context 构建逻辑，不重复处理文档。
差异仅在于：打标输出 ground truth label，审核输出 finding + 批注。

### 7.2 D0-D8 并行流水线

```
D0 质量门控（串行，必须先过）
    │ pass
    ├────────────────────────────────┐
    ▼                                ▼
[高优先级 · 并行]             [中优先级 · 并行]
D1 结构完整性                  D3 语言规范性
D2 内容准确性                  D4 文件引用
D6 人员配备                    D5 业务逻辑
D7 应急处置
    └────────────────────────────────┘
                    ▼
             D8 模板规范性
                    ▼
            结果汇总 + 置信度计算
```

### 7.3 附录与正文联合分析

```python
for ref in body_references:
    appendix = get_chunk(cross_ref_map[ref.target])
    if not appendix:
        finding(HIGH, "悬空引用", evidence=ref.text)
    elif ref.context == "emergency":
        compare_emergency_steps(body_section, appendix)   # D7
    elif appendix.type == "HSE_checklist":
        validate_checklist_fields(appendix, body_section) # D4
```

### 7.4 批量处理

```python
async def batch_audit(doc_paths: list[Path]):
    semaphore = asyncio.Semaphore(5)  # 同时处理5个文档
    tasks = [audit_single(p, semaphore) for p in doc_paths]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    # 超时 >240s → need_human_review=True，全维度返回 uncertain
```

---

## 八、场景二：多模态扩展

### 8.1 图片类型 × 分析内容

```python
# 疏散路线图
{
    "evacuates_both_sides":  True,   # 往两侧疏散，非顺管道方向
    "route_obstacles":       ["河流"],
    "pipeline_marked":       True,
    "three_parallel_lines":  True,   # 管道中心线+左右影响范围线
    "impact_radius_marked":  False,
    "assembly_points_count": 2,
    "legend_correct":        True,
    "confidence":            0.85
}

# 审批签字页（手写识别，Qwen2-VL 直接处理）
{
    "signatures": [
        {"role": "编制", "date": "2025-03-15", "date_confidence": 0.92},
        {"role": "审核", "date": "2025-03-16", "date_confidence": 0.88},
        {"role": "批准", "date": "2025-03-20", "date_confidence": 0.71},
    ],
    "all_roles_signed":    True,
    "date_sequence_valid": True,     # 编制 ≤ 审核 ≤ 批准
}

# 进场路线图
{
    "vehicle_accessible":  True,
    "road_width_adequate": "uncertain",
    "crosses_obstacles":   ["山地"],
    "distance_marked":     True,
}

# 高后果区影像图
{
    "pipeline_visible":       True,
    "building_attributes":    [{"type":"居民楼","distance_marked":True}],
    "impact_zone_marked":     True,
    "scale_bar_present":      True,
}
```

### 8.2 图片分析 → 结构化文本 → 同一套 Agent 处理

```
原始图片
    │
    ▼
Qwen2-VL（针对不同图片类型用不同 Prompt）
    │
    ▼
ImageChunk（description + analysis JSON）
    │
    ▼
存入 SQLite image_chunks 表
    │
    ▼
同一套 2+1 Agent 架构处理（图片内容已文字化）
```

---

## 九、数据库与文件存储

### 9.1 存储选型

```
SQLite：单文件，零部署，Python内置，FTS5全文检索
       30文档×200chunk=6000行，5标准×100条款=500行
       这个量级 SQLite 绰绰有余，PostgreSQL是杀鸡用牛刀

文件系统：只存图片原文件和原始文档，DB里存路径
```

### 9.2 SQLite Schema

```sql
-- 文本块（场景一+二共用）
CREATE TABLE chunks (
    chunk_id      TEXT PRIMARY KEY,
    doc_id        TEXT NOT NULL,
    chunk_type    TEXT,           -- text/table_summary/table_full/appendix/cover/toc
    section_path  TEXT,
    title         TEXT,
    content       TEXT,
    page_start    INT,
    page_end      INT,
    dimensions    TEXT,           -- JSON: '["D1","D7"]'
    cross_refs    TEXT,           -- JSON: '["ASZYQ__APP_C__text__001"]'
    word_count    INT
);
CREATE INDEX idx_chunks_doc ON chunks(doc_id);
CREATE INDEX idx_chunks_dim ON chunks(dimensions);

-- 图片块（场景二）
CREATE TABLE image_chunks (
    chunk_id        TEXT PRIMARY KEY,
    doc_id          TEXT NOT NULL,
    image_type      TEXT,         -- evacuation_route/entry_route/approval/...
    image_path      TEXT,
    parent_chunk_id TEXT,
    description     TEXT,         -- 供FTS5全文检索
    analysis        TEXT,         -- JSON结构化分析结果
    FOREIGN KEY (parent_chunk_id) REFERENCES chunks(chunk_id)
);

-- 国标/企标条款（预处理时按条款切块后存入）
CREATE TABLE standards (
    id            TEXT PRIMARY KEY,  -- "TSG31-2025_3.2"
    standard_name TEXT,
    clause_num    TEXT,
    title         TEXT,
    content       TEXT,              -- 500-800 tokens/条款
    tags          TEXT               -- JSON: '["D2","管道分级"]'
);
-- FTS5全文检索索引（替代grep）
CREATE VIRTUAL TABLE standards_fts
    USING fts5(content, tags, content=standards);

-- 打标结果（ground truth）
CREATE TABLE labels (
    label_id          TEXT PRIMARY KEY,
    doc_id            TEXT,
    dimension         TEXT,           -- "D6"
    explorer_a        TEXT,           -- JSON: {verdict,evidence,confidence}
    explorer_b        TEXT,
    critic            TEXT,           -- JSON: {final_verdict,reasoning,flags}
    final_verdict     TEXT,           -- pass/partial/fail/uncertain
    confidence        INT,
    need_human_review INT,            -- 0/1
    created_at        TEXT
);

-- 批量任务状态
CREATE TABLE batch_jobs (
    doc_id      TEXT PRIMARY KEY,
    doc_path    TEXT,
    status      TEXT,               -- pending/processing/done/failed
    started_at  TEXT,
    finished_at TEXT,
    error_msg   TEXT
);
```

### 9.3 文件目录结构

```
project/
├── audit.db            ← SQLite，所有结构化数据
├── docs/               ← 原始文档
│   ├── AS作业区作业指导书.doc
│   └── 一区一案/
└── images/             ← 从文档提取的图片（DB存路径，文件在这里）
    └── HCQA__2.3__img__001.png
```

---

## 十、输出格式

### 10.1 三种产物

```
每份文档审核完成后输出：
├── audit_result.json   机读：D0-D8全维度结果，含evidence+confidence
├── audit_summary.md    人读：概况报告，风险等级，问题清单
└── annotated_doc.docx  带批注版原文
        ├── 红色批注：HIGH finding
        ├── 黄色批注：MEDIUM finding
        └── 蓝色批注：uncertain（需人工复核）
```

批注通过 chunk 的 `page_start` 定位插入位置。

### 10.2 JSON 输出结构

```json
{
  "doc_id": "ASZYQ-2025-1/D",
  "doc_name": "AS作业区作业指导书",
  "review_timestamp": "2026-04-24T10:00:00+08:00",
  "d0_quality_gate": { "passed": true },
  "dimensions": {
    "D1": {
      "verdict": "partial", "confidence_score": 65,
      "findings": [{"severity":"medium","description":"...","evidence":"..."}]
    },
    "D6": {
      "verdict": "partial",
      "reasoning": "总员工120人 → CEILING(120/100)=2名安全工程师，实际=2 ✓；CEILING(120/20)=6名管理负责人，实际=5 ✗",
      "rules_passed": 3,
      "confidence_score": 92
    }
  },
  "overall_score": 72,
  "need_human_review": false
}
```

---

## 十一、现有资产复用

| 资产 | 路径 | 用途 |
|------|------|------|
| 打标规范 | `作业书打标规范v3.docx` | D0-D8 判定规则 → System Prompt |
| 标准MD | `场景一/MinerU_markdown_*.md` | 已解析，直接按维度加载 |
| 样例输出 | `sample_label_result.json` | JSON Schema 参考 |
| 批量文档 | `一区一案/` 30个文档 | 批量审核测试集 |
| 赛题规则 | `赛题审核规则与补充说明.pdf` | D6人员配备规则原文 |

---

## 十二、实施顺序与当前进度（2026-04-28）

### 已完成（v0.1 vertical slice）

```
Step 1：项目骨架 + LLMProvider 抽象 ✅
  ├─ src/llm/{provider,api_provider,mock_provider,factory}.py
  ├─ config/{default,dimensions}.yaml
  └─ tests/test_llm/* —— 16 tests

Step 2：数据层全集 ✅
  ├─ src/parse/{doc_converter,docx_parser,pdf_parser,scan_detector}.py
  ├─ src/chunk/{models,text_chunk}.py
  ├─ src/store/{schema.sql,repository.py}（SQLite + FTS5 trigram）
  ├─ src/retrieve/fts_search.py
  └─ tests/test_{parse,chunk,store,retrieve}/* —— 38 tests

Step 3：E1 人员配备 vertical slice ✅
  ├─ src/agents/{base,e1_staffing}.py（rule_then_llm 策略）
  ├─ src/pipeline/{audit,label}.py（dual pipeline + 人工签字位）
  ├─ src/output/json_writer.py
  └─ tests/test_{agents,pipeline,output}/* —— 27 tests

Step 4：CLI ✅
  └─ src/cli.py：python -m src.cli {audit|label} <doc>

总计 94 个测试通过；真实 .doc 样本端到端跑通：
  AS作业区作业指导书.doc → E1 verdict=pass，km/worker=3.97
  （文档自述"约5km"，公式判定值在合理区间）
```

### Step 5a 已完成（2026-04-28）：场景一规则维度

```
src/agents/c1_structure.py      —— 5 条检查项
  ├─ check_required_modules    核心模块覆盖（岗位/职责/作业指引/巡检/操作/应急/培训）
  ├─ check_hierarchical_numbering   1.1.1 分级风格（≥60% 命中）
  ├─ check_title_conciseness   标题长度 ∈ [2, 40]
  ├─ check_key_appendices      关键附录 A、C 是否存在
  └─ check_appendix_completeness   附录连续性

src/agents/c4_reference.py      —— 3 条检查项
  ├─ check_appendix_references   "详见附录 X" → 必须有"附录 X"标题
  ├─ check_standard_format     GB/QSY/AQ/TSG 等标准号必须带年份
  └─ check_orphan_anchors      定义但从未引用的附录

src/metrics/compute.py          —— T1/T2/T3
  ├─ compute_t1_template       作业指导书 5 个模板章节覆盖度
  ├─ compute_t2_format         .doc/.docx/.pdf + 解析成功
  └─ compute_t3_latency        ≤60s/120s/180s/240s 四档

src/parse/docx_parser.py 增强   —— 伪标题检测
  ├─ 工业文档常用普通段落写"一、" "1.1" "（一）" 当标题
  └─ _detect_pseudo_heading 用正则兜底，level 1/2/3 区分

src/pipeline/audit.py 重构      —— 多维度调度
  ├─ self.agents = [C1, C4, E1]   每个 dim 独立 try/except，单个挂不影响其他
  ├─ compute_metrics(ctx)         T1-T3 在 agent 之外跑
  └─ _aggregate                   overall_verdict/score 汇总

测试新增 82 个，总计 178 个全部通过：
  tests/test_agents/test_c1_structure.py  —— 30 tests
  tests/test_agents/test_c4_reference.py  —— 28 tests
  tests/test_metrics/test_compute.py      —— 24 tests
```

### 关键决策回顾

**1) 文档级 vs chunk 级路由**

E1 是细粒度维度（只看人员相关段落），通过 `chunk.dimensions` 预路由。
C1/C4 是文档级维度（看全部标题/全部引用），不预路由，pipeline `_run_agent`
里直接喂全部 chunks。

**2) T1-T3 不是 agent**

T1-T3 与文档内容无关，是运行时指标（模板覆盖率、文件后缀、耗时档位）。
独立放在 `src/metrics/`，pipeline 跑完所有 agent 后从 `MetricsContext` 计算。

**3) 伪标题正则的稳健性**

工业文档中很大比例**不使用 Word 的"标题样式"**，而是靠普通段落 + 编号风格
表达层级。`_PSEUDO_HEADING_PATTERNS` 按从具体到通用排序：

```
1.1.1 → level 3
1.1   → level 2     （正则带 (?!\d) 前瞻避免吞 1.1 的子串）
1.    → level 1
（一）→ level 3
```

文本长度 >40 字直接判非标题，避免把段首 "1. xxx..." 误识别。

### 后续阶段（待实施）

```
Step 5b：场景一 LLM 抽取型（C2 / E2 / L2 / C3）
  ├─ FTS5 检索国标 → LLM 比对覆盖率 → Python 判定
  ├─ 仍是单 LLM，不上 LangGraph
  └─ 每个维度 ~250 行 + 1 个 test 文件

Step 5c：场景一推理型（C5）—— 引入 LangGraph
  ├─ Explorer A（temp=0.2，召回事实）
  ├─ Explorer B（temp=0.0，找候选矛盾对）
  ├─ Critic（仲裁，必要时回读原文）
  └─ StateGraph 状态流转 + 条件边

Step 6：场景二多模态（I1-I8、L1-L6）
  ├─ ImageChunk 路由：Qwen3-VL-4B 分析示意图
  ├─ I1-I8 用 LangGraph（图文协作）
  └─ L1-L6 部分用 LangGraph 部分用 Python

Step 7：批量与产出
  ├─ batch_jobs 表 + 并发控制
  └─ 带批注 DOCX 输出
```

## 十三、E1 vertical slice 实现细节

### 数据流

```
doc_path
  │
  ├─[doc_converter]──→ .docx (LibreOffice)
  │
  ├─[docx_parser]───→ DocxBlock[] (paragraphs/headings/tables/images
  │                                + paragraph_index 全局序号)
  │
  ├─[text_chunk]────→ Chunk[] (按 heading 一级切块 → max_tokens 二级切块
  │                            + table_summary/full 拆分
  │                            + dimensions 路由标签)
  │
  ├─[Repository]────→ SQLite (chunks + chunks_fts + standards + labels)
  │
  └─[E1StaffingAgent]
       ├─ LLM 抽取 (Qwen3-2B JSON 输出)──┐
       │                                  ├─→ merge_facts
       └─ regex_fallback 兜底 ─────────────┘     │
                                                  ▼
                                          evaluate_staffing
                                          （3 条公式：安全工程师/区段长/巡线工）
                                                  │
                                                  ▼
                                          AgentResult
                                          { verdict, score, confidence,
                                            findings, extra.staffing_analysis }
                                                  │
                                                  ▼
                                          Repository.upsert_label
                                          (pipeline='audit', human_signoff=False)
```

### 关键设计决策

**1) LLM 仅做"小任务"，公式走规则代码**

E1 的复杂度在公式判定，不在自然语言理解。因此：
- LLM 只被要求输出固定 JSON 的字段（数字抽取）
- 数字 → Python `evaluate_staffing()` 闭式计算
- 输出 100% 可解释，不依赖模型规模，2B 模型足够

**2) 双重抽取保底**

| 来源 | 优先级 | 何时生效 |
|---|---|---|
| LLM JSON | 高 | 模型可达 + 输出合法 JSON |
| regex_fallback | 中 | LLM 失败 / 字段缺失 |
| StaffingFacts() 默认 | 低 | 两者都没拿到 → verdict=uncertain |

**3) verdict 分层判定**

| 条件 | verdict | score | confidence |
|---|---|---|---|
| 全部规则 passed | pass | 14 | 95 |
| 含 high finding | fail | 5 | 80 |
| 仅 medium/low finding | partial | 10 | 75 |
| 事实不足以判定 | uncertain | 0 | 30 |

uncertain → `need_human_review=True`，进入人工兜底队列。

### 测试覆盖

```
tests/test_agents/test_e1_staffing.py —— 17 tests
  ├─ TestEvaluateStaffing：5 条公式各路径
  ├─ TestDeriveVerdict   ：4 种 verdict 状态
  ├─ TestRegexFallback   ：3 种字段抽取
  ├─ TestE1StaffingAgent ：mock LLM / 兜底 / uncertain
  └─ TestMergeFacts      ：LLM 优先 + fallback 补缺

tests/test_pipeline/test_audit.py —— 8 tests
  ├─ TestHelpers：路由判定
  ├─ TestAuditPipeline：构造 .docx 跑全链路
  ├─ TestPersistence：写 labels 表 with pipeline='audit'
  └─ test_real_doc_end_to_end：真实 .doc 样本（@integration）
```

### 限制与已知问题

- 区段长公式当前简化（同时含天然气和成品油时仅按 30km 算），需在 Step 5
  按管线分长度统计；
- pipeline 中 `_ingest` 仅支持 .docx/.doc，PDF 路径在 Step 6 接入；
- 维度路由当前只对 E1_staffing 起作用，其他维度的 keyword 表待维度
  agent 落地时一并补充。
