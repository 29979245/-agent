## Context

现有后端仅覆盖认证与题目审核：`app/db/models/exam.py` 已有 `ExamRecord` / `Question` / `StudentAnswer` 教学链模型，`app/api/v1` 只有 `auth` / `audit` 两组路由，`app/services/question` 为空壳。本 change 要在这之上补全题库管理、考试生命周期、向量检索、试卷导出四块后端能力。技术约束沿用既有约定：SQLAlchemy 2.0 + Alembic（SQLite/MySQL）、枚举英文代码存储（`DbEnum`）、JSON 结构化字段、teacher+ 权限门控（`require_permission`）。动机见 proposal.md，行为契约见四个能力 spec。

## Goals / Non-Goals

**Goals:**
- 落地题库两级结构（QuestionSet/QuestionSetItem）模型与 `/api/exam-bank/*` CRUD
- 扩展 ExamRecord 并落地六态考试状态机与 `/api/exam/*` 生命周期 API
- 提供 ChromaDB 每知识点一向量索引与两段相似题检索（排除自身）
- 提供 Word/PDF 试卷导出（排版 + 双版本）
- 以追加迁移落地，不破坏既有 17 表初始迁移

**Non-Goals:**
- 前端页面（Phase B，独立 change）
- Agent 工具（search_exam_bank 等，文档 56）
- Grading 态挂接 OCR 批改（文档 51，本阶段仅做状态与结果计算）
- 向量联网搜索兜底（文档 56 的三层搜索第三层）

## Decisions

### 1. ExamRecord 扩列而非新建表
给 `ExamRecord` 追加 `name`（String）、`status`（新枚举 `ExamStatus`）、`question_stats`（JSON），`stats` 保留错题统计。
- 备选：新建 Exam 表 → 否决：破坏既有 `StudentAnswer.exam_id` 外键与教学链结构，且与现有模型重复。

### 2. 六态状态机（含阅卷/归档）
`ExamStatus = draft / published / in_progress / grading / completed / archived`。转换规则：
```
draft ─发布(≥1题,写question_stats)▶ published ─首生作答▶ in_progress
in_progress ─教师触发▶ grading ─finalize(统计)▶ completed ─教师触发▶ archived(终态只读)
draft 可级联删除；发布后删除受限；非法转换返回状态机错误
```
- Grading 由教师显式触发，为文档 51 OCR 批改预留入口。
- 备选：沿用设计文档五态 → 否决：文档 47 验收明确要求六态（创建→发布→进行中→阅卷→完成→归档）。

### 3. 双渠道关联 + 真题库内存加载
真题库按 地区/年份/试卷 三层目录在启动时加载 JSON 到内存（`ExamPaper` / 历史题目对象），不落 `Question` 表。题目关联双渠道：
- 渠道一：`Question` 表按 question_id 命中 → 直接关联
- 渠道二：命中真题库 → 复制构造 `Question` 实体写入题库 → 关联
- 备选：真题全量落库 → 否决：真题量可控、设计文档 §7 明确文件加载机制，内存查询更简单。

### 4. knowledge_points 保持 String 列
`Question.knowledge_points` 维持 `String(500)`，API 层以 `list[str]` 收发、service 层逗号 join/split 转换。
- 备选：改 JSON 列 → 否决：需迁移既有数据，audit API 已按字符串收发，转换成本低。

### 5. 向量检索：每知识点一向量
ChromaDB `PersistentClient`，collection `exam_questions`，cosine+HNSW。每知识点生成独立向量（ID `{exam_id}::kp-N`）；Embedding 用 DashScope text-embedding-v3（1024 维），不可用回退 MD5 伪向量；构建时检测维度不一致自动清库重建。检索两段：关键词匹配 Top-20 初筛 → 候选内向量精筛 Top-K；相似题推荐排除自身。
- 备选：Milvus → 否决：单机嵌入式即可，数据量未达迁移阈值。

### 6. 导出：python-docx + HTML→PDF
Word 用 `python-docx`（A4、上2.5/下2.0/左2.5/右2.0cm、正文 SimSun 11pt、标题 16pt 加粗居中、密封线区、按题型分节、化学式数字下标富文本）；PDF 复用 HTML 报告（`generate_report_html`，教师版/学生版）转 PDF，导出前注册 SimSun 防中文乱码。
- 备选：reportlab 直接绘制 → 否决：HTML 报告复用度高、改版成本低。

## Risks / Trade-offs

- [向量 Embedding 依赖 DashScope API，网络不稳] → MD5 伪向量兜底 + ChromaDB 不可用降级纯关键词检索
- [PDF 中文显示方框] → 导出时注册 SimSun 字体（Windows）
- [化学式下标富文本解析复杂] → 先支持 `_数字`/`^` 模式，复杂公式保留 KaTeX 源文本
- [Grading 态暂未接 OCR（文档 51）] → 本阶段仅状态与结果计算，51 挂接时按状态机入口接入
- [真题库 JSON 缺失/损坏] → 加载容错，跳过坏文件并日志告警，不阻断启动

## Migration Plan

- 新增一条 alembic revision：创建 `question_set`、`question_set_item` 表；`exam_record` 加 `name` / `status` / `question_stats` 列
- 幂等：`alembic upgrade head` 可重复执行；回滚 `alembic downgrade` 仅撤销新表与新列，既有表结构不变

## Open Questions

无——状态机语义、字段扩展、转换规则已在 grilling 阶段与用户定稿，不构成可推迟的未知项。
