## Why

学生「我的 → 学习计划」弹窗空态文案写「暂无学习计划，完成练习后可生成」，但系统从未有自动生成逻辑：`student.learning_plan` 的唯一写入方是教师专用 Agent 工具 `send_learning_plan`，且教师端「生成学习计划」按钮 `disabled`（功能建设中）、doc 22 规格的 REST 端点全部缺失。结果是学习计划永远无法生成，空态文案误导学生以为做完练习会自动出现。

## What Changes

- **新增 diagnosis REST 端点**（doc 22 §2.6 规格，回归设计 doc 30 §3.3 教师生成路径）：
  - `POST /api/diagnosis/learning-plan/generate` — 教师为指定学生**生成计划预览**（规则引擎，不落库）。
  - `POST /api/diagnosis/learning-plan/apply/{student_id}` — 教师**推送计划**：写入 `student.learning_plan` 并通知绑定家长。
  - `GET /api/diagnosis/learning-plan/{student_id}` — 读取计划；学生仅读自身，教师可读任意。
  - 权限：generate/apply 走 `diagnosis:create`（教师+）；get 走 `diagnosis:read`（学生仅自身）。
- **新增规则引擎学习计划生成器**：基于学生知识点掌握度（薄弱点）与障碍画像，确定性生成 `plan_text + plan_data`（标题 + 每日任务列表）。诊断 LLM 尚未接入（`_chat` 桩），故不走 LLM。
- **教师端 students.html**：启用「生成学习计划」按钮，两步交互（生成预览 → 确认推送）；支持深链 `?focus=<student_id>&action=plan`（对应 `generate_learning_plan` Agent 工具的路由指令）自动打开抽屉并触发生成。
- **学生端 profile.html**：空态文案改为「暂无学习计划，等待老师为你生成」；`renderPlan` 支持新版 `plan_text + plan_data` 每日任务渲染（doc 30:788）。
- **测试**：生成器（薄弱点提取、格式、无数据兜底）+ 端点（教师专属、apply 落库+通知、get 所有权）。

## Capabilities

### New Capabilities

（无 — 本 change 全部映射到既有能力）

### Modified Capabilities

- `diagnosis-api`: 新增「学习计划生成与推送」Requirement（3 个 REST 端点 + 生成器行为 + 权限）。
- `teacher-panel-frontend`: 新增「教师生成学习计划」Requirement（学生详情抽屉生成/推送交互 + 深链自动触发）。
- `student-frontend`: 修改「学习周报与学习计划展示」Requirement（空态文案语义 + 新版 plan_text/plan_data 渲染）。

## Impact

- `app/api/v1/diagnosis.py` — 新增 3 个端点，挂载于已有 `diagnosis_router`（/api/diagnosis）。
- `app/services/diagnosis/learning_plan.py`（新增）— 规则引擎生成器；复用 `report_service` 的知识点掌握度聚合与 `send_learning_plan` 的家长通知逻辑。
- `app/agents/tools/tools_diagnosis.py` — `send_learning_plan` 复用通知逻辑（可选小重构，避免重复）。
- `frontend/pages/students.html` — 启用按钮、预览/推送交互、深链处理。
- `frontend/pages/profile.html` — 空态文案、`renderPlan` 新版格式渲染。
- `tests/unit/` — 新增 `test_learning_plan.py`（生成器 + 端点），或并入 `test_diagnosis_api.py`。
- 无数据模型变更（`Student.learning_plan` 已存在），无迁移。
