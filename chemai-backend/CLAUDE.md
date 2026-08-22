# ChemAI 智辅化学 — 后端开发规范

面向中学化学教学的 AI Agent 系统。教师端出题/审核/OCR 批改/障碍诊断，学生端自适应练习与间隔复习，家长端学情报告。领域术语见 `CONTEXT.md`。

## 技术栈（依据设计文档 38-技术选型与工具链）

| 层 | 选型 | 备注 |
|----|------|------|
| Web | FastAPI 0.109 + Uvicorn 0.27 | 原生 async、SSE 流式 |
| ORM/迁移 | SQLAlchemy 2.0 + Alembic | 23 个模型，切库只改连接串 |
| 数据库 | SQLite + WAL（开发）/ MySQL（生产） | 三库分离：主库、Agent 检查点、Agent 长期记忆 |
| 向量库 | ChromaDB 0.4.22 + DashScope text-embedding-v3 | 1024 维，检索真题 |
| LLM | 三级 Fallback：MiMo-V2.5 → 通义千问 qwen-turbo → DeepSeek-V4-Flash | 每级 3 次重试 + 指数退避；仅国内 Provider |
| Agent | LangGraph `create_react_agent` 单 Agent（v2） | v1 多 Agent 保留为回退，`request.version` 切换 |
| 化学 | RDKit 2024.3.1 + 自研系数配平算法 | 配平必须确定性算法，100% 零误差，不靠 LLM 配平 |
| OCR | 百度教育 OCR（主力）/ MinerU（PDF）/ VLM（兜底） | 手写用百度，印刷用 MinerU |
| 调度 | APScheduler 3.10.4 | 每日练习 08UTC、预警检测 00UTC、OCR 轮询 5s |
| 测试 | pytest 8.0 | 单元 + 集成 + Evals |
| 部署 | Docker Compose（python:3.11-slim） | 桌面端 pywebview + PyInstaller |

## 开发流程（依据 00-总体开发方案）

五层工具链：
- **思考层**：`/plan-eng-review`（架构决策）；产品方向分歧才用 `/office-hours` `/plan-ceo-review`
- **规格层**：`/opsx:propose` → `/opsx:apply` → `/opsx:archive`（OpenSpec）
- **实现层**：TDD（写测试 RED → 生成实现 GREEN → pytest 验证）
- **质量层**：Evals（L1/L2/L3 金字塔 + 基线对比）
- **流程层**：`/review` `/qa` `/ship` `/retro` `/investigate`

设计文档已定义清楚的功能**直接 propose**，不重复开会讨论。

### 质量门禁
- pre-commit → `pytest tests/ --tb=short -x`（L1 单元，<5 秒）
- commit-msg → 校验 Conventional Commits 格式
- pre-push → `run_evals --tier all --compare baseline.json`（劣化 >5% 阻断）
- 劣化处理：≤3% 记 devlog；3-5% 用 `/investigate` 修复；>5% `/investigate` 后 `git revert` 或修复 commit

### Evals 三层金字塔
- **L1 单元**：纯函数逻辑（规则引擎、置信度、状态机）≥95%
- **L2 集成**：API 端点行为（结构、状态码、认证）≥90%
- **L3 质量**：AI 内容（科学性、诊断准确率、辅导安全性）≥70%，基于 100 条 Golden 数据集

## Git 规范
- 原子化提交，不攒批；每个模型/组件独立 commit
- Conventional Commits：`{type}({scope}): {description}`
- 类型：feat / fix / test / refactor / docs / chore / perf
- 范围：model / auth / review / exam / diagnosis / ocr / agent / eval / persona / guard / ui / golden-NNN
- 版本节奏：阶段完成 `git merge --no-ff` → `git tag -a v0.N.0`；Evals 通过 → `git tag evals-ok-YYYYMMDD`

## 目录结构

```
app/
  main.py             FastAPI 入口
  config.py           全局配置（三库路径、LLM/OCR Provider）
  core/               JWT、权限矩阵、依赖注入、统一错误码
  db/models/          23 个 SQLAlchemy 模型（org/user/exam/question/diagnosis/review/parent/ocr）
  api/v1/             路由：auth/exam/question/diagnosis/ocr/exercise/analytics/parent/agent/mcp
  services/
    question/         出题生成管线（RAG→生成→审核→入库）、真题库、向量检索、知识图谱、导出
    audit/            四维安全审核引擎（系数配平/反应条件/产物/分子结构）
    diagnosis/        规则引擎 + LLM 混合诊断、障碍画像聚合、教师配置
    exercise/         自适应练习（ZPD）+ 间隔复习（艾宾浩斯 6 级状态机）
    analytics/        学情面板 + 预警引擎
    ocr/              UploadSession 状态机、三引擎、LLM 批改、OCRTask 队列
  agents/
    core/             Gateway/Planner/Context Manager/GuardState/记忆/审计/模型工厂/SSE 适配器
    personas/         4 个 Persona YAML（teacher/student/tutor/parent）
    tools/            工具组（出题与题库/诊断与学生/辅导/OCR批改/记忆/家长报告/浏览器）
    chem_skills/      10 个化学技能（苏格拉底辅导、模拟实验、配平等）
    factories/        v1/v2 Agent 工厂 + 辅导工具工厂
    mcp/              MCP 工具服务器（16 个 MCP 工具）
  evals/              Golden 数据集、L1/L2/L3 运行器、基线
tests/                单元 / 集成 / 评测
```

## 关键约定
- 三个数据库文件固定位于 `data/`，不写入版本库
- Agent 对话使用 SSE 流式输出；审批类操作必须走 GuardState 第 4 层审批门控
- 化学方程式一律 KaTeX/LaTeX 格式，经化学式归一化函数处理
- 修改代码后运行 `graphify update .` 保持知识图谱同步
