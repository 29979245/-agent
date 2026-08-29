## Why

设计文档 30 §3.4–3.7 定义了辅导、OCR 批改、记忆、家长报告四类 Agent 工具（16 个），但当前工具注册表仅有出题组 + 诊断组 14 个工具。学习闭环中的学生辅导、教师批量批改、记忆读写与家长报告环节 Agent 尚无法触达，本变更补齐这一切片。

## What Changes

- **新增辅导组 9 工具**：6 个苏格拉底式专题辅导（`ionic_equation_tutor` / `stoichiometry_tutor` / `redox_tutor` / `equilibrium_tutor` / `periodic_law_tutor` / `organic_tutor`，同一工厂函数生成）+ 通用辅导 `chemistry_tutor`（教师 800 字教研分析 / 学生 500 字引导教学）+ 模拟实验 `simulate_experiment`（LLM 生成实验报告）+ 配平方程式 `balance_equation`（确定性配平 + 四维审核，不靠 LLM 配平）。
- **新增 OCR 批改组 3 工具**：`query_ocr_progress`（批次进度聚合，只读）、`grade_answer_sheets`（对已完成 OCR 任务批量 LLM 批改，只算不落库）、`save_grading_results`（逐学生写入作答并触发障碍诊断）。后两者为写工具，纳入 Guard 第 4 层审批门控。
- **新增记忆组 2 工具**：`memory_student_get`（读取学生诊断历史最近 5 条 + 当前学习计划，全体角色）、`memory_teacher_get`（读取教师偏好，仅教师）。接线 `LongTermStore` 读路径，并补齐诊断完成后的记忆写路径（`push_student_diagnosis`），使读取有数据可依。
- **新增家长报告组 2 工具**：`generate_parent_report`（聚合练习/诊断/知识点生成家长可读报告预览，不发送）、`send_report_to_parent`（推送周报到已绑定家长通知列表，写工具走审批门控）。家长角色按既有 `家长班级诊断受限` 门控隔离。
- **注册表 14 → 30**：`app/agents/tools/` 新增 `tools_tutoring.py` / `tools_ocr.py` / `tools_memory.py` / `tools_parent_report.py`；`tool_meta.py` 扩展 TOOL_META（限次/角色）；persona 白名单按 doc 30 §4.2 更新。
- **Guard 审批集扩展**：`APPROVAL_TOOLS` 纳入 `grade_answer_sheets` / `save_grading_results` / `send_report_to_parent`。

## Capabilities

### New Capabilities

无新能力。四组工具沿用现有 `agent-core` 能力的工具切片组织方式（同出题组/诊断组）。

### Modified Capabilities

- `agent-core`: 新增 4 个工具切片要求——`工具切片——辅导组`、`工具切片——OCR批改组`、`工具切片——记忆组`、`工具切片——家长报告组`；每个要求覆盖对应工具的输入/限次/角色约束、关键行为与审批门控场景。

## Impact

- **代码**：`app/agents/tools/`（新增 4 文件 + `registry.py` 注册 16 工具 + `tool_meta.py` 扩展）、`app/agents/guard.py`（`APPROVAL_TOOLS` 扩展）、`app/agents/memory.py`（接线 `push_student_diagnosis` 写路径）、`app/agents/personas/*.yaml`（白名单更新）、`app/agents/context.py`（若需为辅导工具注入题目上下文）。
- **复用**：`app/services/audit/equation.py`（`balance_equation`）、`app/services/ocr/batch.py`（`aggregate_tasks` / `run_grading` / `save_grading_results`）、`app/services/analytics/report_service.py` + `parent_service.py`（家长报告）、`app/services/exercise/spaced_repetition.py`（学习计划读取）。
- **测试**：单元工具测试（含 6 专题工厂、配平确定性、审批门控、记忆读写接线、家长角色门控）+ 集成 persona 白名单测试 + evals `agent_tools` 注册检查更新（30 工具）。
- **无破坏性变更**：不修改既有 14 工具契约；OCR/记忆/报告均为新增工具。
