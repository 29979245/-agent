## 1. 辅导组工具（9 个）

- [x] 1.1 创建 `app/agents/tools/tools_tutoring.py`：6 专题工厂 `_make_topic_tutor(topic, method_steps)`（苏格拉底四步法方法字典）+ 独立实现 `chemistry_tutor`（按 `ctx.user.role` 分流 800 字教研/500 字引导）/ `simulate_experiment`（实验报告八段）/ `balance_equation`（复用 `audit.equation.check_balance`/`count_elements` 确定性配平），并为每工具定义 pydantic Args 模型；验证 `python -c "import app.agents.tools.tools_tutoring"` 无导入错误
- [x] 1.2 写 `tests/unit/test_agent_tools_tutoring.py`：6 专题工厂生成的方法分流、`chemistry_tutor` teacher/student 角色分流、`balance_equation` 返回两侧原子计数且相等（不依赖 LLM）、`simulate_experiment` 返回八段结构；验证 `pytest tests/unit/test_agent_tools_tutoring.py -q` 通过
- [x] 1.3 `registry.py` 注册 9 工具 + `tool_meta.py` 新增 9 条 TOOL_META（限次 5/3/2/3，角色按 doc 30 §3.4）；验证 `build_langgraph_tools` 对 student/tutor persona 不再告警未注册辅导工具

## 2. OCR 批改组工具（3 个）

- [x] 2.1 创建 `app/agents/tools/tools_ocr.py`：`query_ocr_progress`（`aggregate_tasks` 只读聚合进度）、`grade_answer_sheets`（`run_grading` 只算不落库）、`save_grading_results`（`save_grading_results` 写 StudentAnswer + 触发诊断，`_student_by_no` 学号校验），每工具校验 `ctx.user.role == "teacher"` 否则 ForbiddenError；验证无导入错误
- [x] 2.2 Guard 接线：`guard.py` `APPROVAL_TOOLS` 加入 `grade_answer_sheets` / `save_grading_results`，`PREREQUISITES` 补 batch_id/exam_id 非空校验；验证 guard 单元测试注入新 approval 工具返回 `requires_approval_blocked`
- [x] 2.3 写 `tests/unit/test_agent_tools_ocr.py`：进度聚合结构、`grade_answer_sheets` 不落库（mock LLM）、`save_grading_results` 写库并触发诊断、非 teacher 返回 ForbiddenError、未审批调用返回 `requires_approval_blocked`；验证 pytest 通过

## 3. 记忆组工具（2 个）

- [x] 3.1 创建 `app/agents/tools/tools_memory.py`：`memory_student_get`（`ctx.memory.long_term.student_diagnosis_history` 最近 5 条 + `student.learning_plan`，student 仅读自身跨生 ForbiddenError）、`memory_teacher_get`（`teacher_pref`，仅 teacher）；验证无导入错误
- [x] 3.2 记忆写接线：`diagnose_barrier` 个体诊断落库后调用 `push_student_diagnosis`；`save_grading_results` 触发诊断后调用 `push_student_diagnosis`（best-effort 失败不阻塞）；验证接线点存在且诊断完成即写库
- [x] 3.3 写 `tests/unit/test_agent_tools_memory.py`：学生读自身记忆（预置历史断言最近 5 条 + 学习计划）、跨学生 403、教师读偏好、诊断写记忆后读回（含 LLM 降级规则路径）；验证 pytest 通过

## 4. 家长报告组工具（2 个）

- [x] 4.1 创建 `app/agents/tools/tools_parent_report.py`：`generate_parent_report`（复用 `weekly_report_service.generate_weekly_report` 聚合，返回预览 + `requires_confirmation`，不发送）、`send_report_to_parent`（`StudentParentBinding(status=active)` → 逐家长写 `ParentNotification(weekly_report)` → 返回发送确认 + 已通知数），两者校验仅 teacher、parent 角色返回 ForbiddenError；验证无导入错误
- [x] 4.2 Guard 接线：`APPROVAL_TOOLS` 加入 `send_report_to_parent`，`PREREQUISITES` 补 student_id 非空；验证未审批调用返回 `requires_approval_blocked`
- [x] 4.3 写 `tests/unit/test_agent_tools_parent_report.py`：生成预览不写通知、发送写 ParentNotification 并返回已通知家长数、审批阻塞、parent 角色 403；验证 pytest 通过

## 5. 注册表 / Persona / 集成 / 评测

- [x] 5.1 `personas/*.yaml` 白名单补齐（student 4 专题 + chemistry_tutor + simulate_experiment；tutor 不变；teacher 增 OCR 3 + memory_teacher_get + 报告 2）；验证 `tests/integration` persona 白名单测试断言 persona 工具集全部已注册、`build_langgraph_tools` 无未注册告警
- [x] 5.2 `app/evals/agent_tools.py` 注册检查 14 → 30 更新；验证 `python -m app.evals.runners.run_evals --tier all --compare app/evals/baseline.json` 无劣化（<5%）
- [x] 5.3 全量 `pytest tests/ -q` 通过 + 运行 `graphify update .` 同步知识图谱
