## Why

后端题库管理 / 考试生命周期 / 导出 API 已全部落地并归档（2026-08-24-exam-bank-api），但前端工作台 Tab 2/3/4 仍走 mock（`USE_MOCK=true`），且 Tab 2 布局（下拉框+扁平列表）与设计目标（文件夹侧栏+卡片网格）差距明显、Tab 4 状态模型与后端六态状态机错位（使用了后端不存在的 `AddingQuestions` 状态）、后端还缺少考试列表查询端点。本次将前端 Tab 2/4 重构为真实接口驱动的完整题库管理与考试管理闭环。

## What Changes

- **Tab 2 题库管理重构**：左侧 QuestionSet 文件夹目录（系统预设置顶 + 当前教师自己的并集）+ region/year 组合筛选条 + 右侧题目卡片缩略网格（审核状态角 / 题型标签 / 难度徽章）+ 分页控件；新建/删除文件夹、查看文件夹题目、批量操作（加入考试 / 从题库移除 / 批量导入）。
- **批量导入**：对接 `POST /api/exam-bank/exam-sets/{set_id}/import-questions`；Tab 1 出题工作台增加「保存到题库文件夹」下拉，批准入库时将已生成的题导入选中文件夹。
- **Tab 4 考试列表对齐六态**：删除 `AddingQuestions`，补齐 `Grading`/`Archived`，六种状态各有对应 chip 标签；每状态按后端状态机显示条件按钮（Draft=编辑/发布/删除；Published|InProgress=开始阅卷/导出；Grading=完成统计/导出；Completed=归档/导出/看结果；Archived=只读导出）；「编辑」以抽屉弹窗呈现加题/移题。
- **导出接入**：Tab 4 「导出」调用 `GET /api/question/export/{record_id}?format=docx|pdf&with_answers`。
- **数据层切换真实接口**：`USE_MOCK=false`，`api.js` 路径与真实路由对账（`/api/exam-bank/exam-sets`、`/api/exam-bank/papers`、`/api/exam-bank/historical`、`/api/exam/...`、`/api/classes`、`/api/knowledge/list`）。
- **后端补考试列表与班级端点**：新增 `GET /api/exam` 分页列表（id 倒序），返回考试概要（exam_id/name/class_name/question_count/status/exam_date）；新增 `GET /api/classes` 班级列表支撑创建考试表单。

## Capabilities

### New Capabilities

无（前端承接既有 `exam-workbench` 能力，后端复用 `exam-lifecycle`）。

### Modified Capabilities

- `exam-workbench`：题库管理（Tab 2）需求升级为「文件夹侧栏 + 筛选 + 卡片网格 + 分页 + 批量导入」；考试列表（Tab 4）需求升级为「六态状态标签 + 每状态条件按钮 + 编辑抽屉」；新增真实接口对接与 mock 下线。
- `exam-lifecycle`：新增「考试列表查询」需求——提供 `GET /api/exam` 分页列表端点，支撑前端 Tab 4 考试卡片列表。

## Impact

- 前端：`chemai-backend/frontend/pages/exam-v2.html`、`frontend/pages/js/workbench.js`、`frontend/pages/js/api.js`、`frontend/pages/js/mock.js`（mock 数据下线或仅留兜底）。
- 后端：`app/api/v1/exam.py`（新增 `GET /api/exam` 路由）、`app/services/question/exam_service.py`（新增列表查询方法）、`app/services/question/exam_bank.py`（`list_sets` 支持「预设 + 我的」并集展示）、`tests/unit/test_exam_api.py`。
- 设计系统：Tab 2/4 布局与组件对齐 doc 36（实验室笔记本主题可选增强）。
