# IndustryAgent

工业 AI 审核系统：覆盖场景一（作业指导书文本审核）+ 场景二（高后果区风险管控方案多模态审核）。

## 目录结构

```
industryAgent/
├── src/                # 源码
│   ├── llm/            # LLMProvider 抽象（vLLM / OpenAI-compatible API / Mock）
│   ├── parse/          # .doc/.docx/PDF 解析、扫描件 OCR 兜底
│   ├── chunk/          # 文本/表格/图片切块
│   ├── store/          # SQLite schema + repository
│   ├── retrieve/       # FTS5 关键词检索 + 维度路由
│   ├── agents/         # 维度 Agent（Explorer/Critic）
│   ├── pipeline/       # 审核 / 打标 双流水线
│   └── output/         # JSON / MD / 带批注 DOCX 输出
├── tests/              # 与 src/ 一一对应的测试
│   └── fixtures/       # 测试用文档
├── config/
│   ├── default.yaml    # 模型、路径、维度路由配置
│   └── dimensions.yaml # 赛题维度定义
├── data/
│   ├── audit.db        # SQLite
│   ├── docs/           # 原始文档
│   └── images/         # 提取的图片
├── docum/              # 赛题资料、参考文档、原始样本
├── pyproject.toml
└── requirements.txt
```

## 快速开始

### 1. 安装依赖
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128

```bash
pip install -e .
# 或
pip install -r requirements.txt
```

系统依赖：
- LibreOffice（用于 `.doc` → `.docx` 转换）：`apt install libreoffice`

### 2. 准备模型服务

默认通过 OpenAI-compatible API 调用本地模型。推荐用 vLLM 起一个：

```bash
# 文本模型（起步用 Qwen3-2B）
vllm serve Qwen/Qwen3-2B --port 8000

# 视觉模型（起步用 Qwen3-VL-4B）
vllm serve Qwen/Qwen3-VL-4B --port 8001
```

也可以在 [config/default.yaml](config/default.yaml) 中切到 mock provider 跑测试。

### 3. 运行审核

```bash
python -m src.cli audit docum/AS作业区作业指导书\(1\).doc
```

输出 `data/results/<doc_id>/audit_result.json`。

### 4. 跑测试

```bash
pytest tests/ -v
```

## 设计文档

详见 [docum/system_design.md](docum/system_design.md)。

## 维度体系

按赛题评分表对齐：

**场景一（作业指导书）**
- C1 结构完整性 / C2 内容完整性 / C3 文字语法 / C4 引用文件可追溯 / C5 业务逻辑
- E1 人员配备 / E2 应急处置
- T1 模板使用 / T2 格式兼容 / T3 识别效率

**场景二（一区一案）**
- I1-I8 图片识别能力（签字/必备图/影像图/入场/逃生/水体/管网/一致性）
- L1-L6 上下文逻辑识别（一致性/标准/必备章节/时间链/数据范围/文字模板）
- T1-T3 同场景一

## 当前进度

- [x] Step 1 项目骨架 + LLMProvider 抽象
- [x] Step 2 数据层（解析 + 切块 + 存储 + FTS5 检索）
- [x] Step 3 E1 人员配备 vertical slice（rule_then_llm）
- [x] Step 4 CLI + 真实样本跑通
- [x] **Step 5a 场景一规则维度：C1 结构 + C4 引用 + T1-T3 metrics（178 tests）**
- [ ] Step 5b 场景一 LLM 抽取型：C2 内容 / E2 应急 / L2 标准 / C3 语言
- [ ] Step 5c 场景一推理型：C5 业务逻辑（LangGraph 2+1 Agent）
- [ ] Step 6 场景二多模态：I1-I8、L1-L6
- [ ] Step 7 批量与带批注输出

### Demo 输出（真实样本，6 个维度）

```
$ bash scripts/run.sh audit "docum/AS作业区作业指导书.docx"

[audit] AS作业区作业指导书.docx
  overall_verdict: ...
  overall_score:   ...

  C1_structure         verdict=...  score=15  确认核心模块/章节编号/附录
  C4_reference         verdict=...  score=12  附录引用闭环 / 标准编号格式
  E1_staffing          verdict=...  score=14  公式：每3-10km 1 名巡线工
  T1_template          verdict=...  score=4   作业指导书模板覆盖度
  T2_format            verdict=pass score=4   .doc/.docx/.pdf 兼容
  T3_latency           verdict=pass score=4   ≤60s 第一档
```

每个维度独立打分、独立 finding，最终 overall 由 `_aggregate` 汇总。
