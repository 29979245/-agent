## Context

后端题库管理 / 考试生命周期 / 导出 API 已落地并归档（见 proposal.md - Why）。前端为单页工作台 `chemai-backend/frontend/pages/exam-v2.html` + 若干 JS 模块（Vue 3 全局版 + KaTeX + mock 适配层），Tab 2/3/4 当前走 `USE_MOCK=true`。

数据模型约束（读模型确认）：
- `ExamRecord` 仅有 `class_id`（FK→class），**无 `teacher_id`、无 `created_at` 列**；`Class.head_teacher` 只是展示字符串，无教师 FK。→ 考试列表**无法按教师过滤**，也**无法提供 created_at**。
- 后端 `app/api/v1/` 仅 4 个路由文件（auth/exam/exam_bank/audit），**无 `/api/classes`、无 `/api/knowledge`**。
- `question_dict`（共享序列化）不含 `audit_status`，卡片状态角无数据源。

## Goals / Non-Goals

**Goals:**
- Tab 2 重构为真实接口驱动的文件夹侧栏 + region/year 筛选 + 卡片缩略网格 + 分页 + 批量导入
- Tab 4 对齐后端六态状态机（删 AddingQuestions，补 Grading/Archived）+ 每态条件按钮 + 编辑抽屉
- 后端补 `GET /api/exam`（分页列表）与 `GET /api/classes`（班级下拉）
- mock 下线，Tab 2/3/4 全走真实接口
- 导出接入 `GET /api/question/export/{record_id}`

**Non-Goals:**
- 不改数据模型（不新增 teacher→exam 关联、不迁移 created_at、不做教师作用域）
- 不做 RAG/AI 出题管线、不做 doc25 §5.3 自动命名文件夹流程
- 不补 `/api/knowledge` 端点——Tab 1 知识点云保留前端静态列表（非本 change 核心范围）

## Decisions

### D1 考试列表不分教师、用 exam_date
`ExamRecord` 无 teacher 关联，列表 `GET /api/exam` 分页返回全部考试（`ORDER BY id DESC` 近似创建顺序）。`class_name` 经 `ExamRecord.class_` join `Class.name`；`question_count` 用 `COUNT(Question.record_id == exam_id)`；卡片展示改用「考试日期 exam_date」。**替代方案**：按教师作用域需加 teacher→class 关联（数据模型变更），超范围。按创建时间需要迁移 created_at 列，故弃。

### D2 新增 `GET /api/classes` 班级列表
创建考试表单的班级下拉需要真实数据。新路由返回 `{items: [{id, name}]}`（`Class` 表，按 id 排序）。挂在 `app/api/v1/exam.py` 下（同考试域）即可，无需新路由文件。

### D3 `list_sets` 支持「预设 + 我的」并集
传 `teacher_id=X` 时过滤条件改为 `teacher_id.in_([0, X])`（预设 teacher_id=0 恒包含），排序 `is_preset DESC, id DESC` 使系统预设置顶。改动集中在 `ExamBankService.list_sets`。

### D4 `question_dict` 增补 `audit_status`
共享序列化器追加 `audit_status` 字段（`q.audit_status.value`），为 Tab 2 卡片网格「审核状态角」提供数据。additive 改动，exam_service / exam_bank 复用方不受影响。

### D5 api.js 路径对账（真实路由替换 mock）
| 旧（mock） | 真实路由 |
|-----------|---------|
| `searchQuestions(setId)` → `GET /api/question/search` | `GET /api/exam-bank/exam-sets/{set_id}`（详情含题目） |
| `searchHistorical(q)` → `GET /api/question/historical` | `GET /api/exam-bank/historical` |
| `getExams()` → `GET /api/exam/list` | `GET /api/exam`（新增） |
| `updateExam(id)` PATCH | **删除**——无对应端点；状态变更走 publish/start-grading/finalize/archive 专用端点 |
| `getKnowledge()` → `/api/knowledge/list` | 前端静态知识列表（见 D7） |
| `getClasses()` → `/api/classes` | `GET /api/classes`（新增） |

新增 api.js 方法：`importQuestions`、`getExamSetDetail`、`addExamQuestions`、`removeExamQuestion`、`publishExam`、`startGrading`、`finalizeExam`、`archiveExam`、`exportExam`。

### D6 导出走 fetch blob（非 window.open）
导出接口需带 `Authorization` 头，`window.open` 无法携带 → `fetch(url, {headers})` → `blob` → `URL.createObjectURL` → `<a download>` 触发下载。参数 `format=docx|pdf`、`with_answers`。

### D7 Tab 1 知识点云降级为静态列表
后端无 knowledge 端点，Tab 1 AI 表单的知识点云改用前端常量数组（含常用化学知识点），generate 仍走真实接口。已知局限，不在本 change 修复。

### D8 Tab 2 卡片网格字段来源
- 审核状态角 ← `audit_status`（D4）
- 题型标签 ← 前端推断：`options.length > 0` → 选择题，否则按 answer/analysis 长度区分填空/简答
- 难度徽章 ← `difficulty`
- 题干经 KaTeX 渲染、知识点标签 ← `knowledge_points`

### D9 Tab 4 六态 chip + 每态按钮（computed）
| 状态 | 文案 | 按钮 |
|------|------|------|
| Draft | 草稿 | 编辑 / 发布 / 删除 |
| Published | 已发布 | 开始阅卷 / 导出 |
| InProgress | 进行中 | 开始阅卷 / 导出 |
| Grading | 阅卷中 | 完成统计 / 导出 |
| Completed | 已完成 | 归档 / 导出 / 看结果 |
| Archived | 已归档 | 导出（只读） |

「编辑」= 抽屉弹窗：列出考试题目（`GET /api/exam/{id}/questions`），可从题库/真题选入（add_questions，支持渠道二 str ref_id）、移除单题。

## Risks / Trade-offs

- [后端新端点回归] → TDD：`GET /api/exam`、`GET /api/classes`、`list_sets` 并集、`question_dict.audit_status` 各补单元测试。
- [前端大改回归] → 改完后浏览器实测 Tab 1/2/3/4 黄金路径 + 各状态按钮，确认 KaTeX 渲染与审核卡片不回归。
- [考试列表无 created_at/教师作用域] → 展示 exam_date、列表不分教师；与原型「创建于」文案有出入，属数据模型取舍。
- [knowledge 无端点] → 静态列表降级，Tab 1 知识云非真实数据（已知局限）。

## Migration Plan

1. 后端先行：D1/D2/D3/D4 + 测试 → `GET /api/exam`、`GET /api/classes`、`list_sets` 并集、`question_dict.audit_status`
2. 前端：D5 路径对账 → D8 Tab 2 重构 → D9 Tab 4 六态+抽屉 → D6 导出 → D7 知识降级
3. 回滚：保留 mock.js，`USE_MOCK` 可瞬间切回；后端端点先行合并即可独立回滚
