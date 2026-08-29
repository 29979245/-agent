## Context

Agent 核心已就绪（Gateway→ReAct→SSE→记忆→审计→Provider 回退），工具注册表现有 14 工具（出题组 7 + 诊断组 7），Guard 四层护栏 + persona 白名单交集过滤，MCP 16 RPC 工具与 Agent 自主路径独立。本 change 新增 16 工具：辅导 9、OCR 3、记忆 2、家长报告 2（动机见 proposal.md — Why）。

关键复用面已存在，均薄封装不重写：

- `app/services/audit/equation.py`：`check_balance` / `audit_equation` / `count_elements`（确定性配平，非 LLM）。
- `app/services/ocr/batch.py`：`aggregate_tasks` / `run_grading` / `save_grading_results` / `_student_by_no` / `get_session_or_404`。
- `app/agents/memory.py` `LongTermStore`：`student_diagnosis_history` / `teacher_pref` / `push_student_diagnosis`（写 best-effort），经 `ctx.memory.long_term` 可达。
- `app/services/analytics/`：`report_service.build_student_report`、`parent_service.build_parent_report`、`weekly_report_service.generate_weekly_report`。
- `send_learning_plan`（tools_diagnosis.py L378）是「持久化 + ParentNotification 通知」写工具的先例：`StudentParentBinding(status=active) → ParentNotification(notification_type=...)`。
- `ToolContext` 提供 `db / user / guard / memory / search / llm / emit / persona`，工具实现不碰全局单例。

## Goals / Non-Goals

**Goals:**

- 16 工具全部按既有 `register_impl(name, schema, fn)` 模式接入，TOOL_META 扩展限次/角色。
- 写工具（`grade_answer_sheets` / `save_grading_results` / `send_report_to_parent`）纳入 Guard L4 审批门控。
- 记忆读工具有数据可依：接线 `push_student_diagnosis` 写路径。
- 6 专题辅导复用同一工厂（doc 30 §8.2），`balance_equation` 复用确定性配平。
- 家长角色对报告/诊断的班级级访问维持既有门控（ForbiddenError）。

**Non-Goals:**

- 不改 MCP 工具（16 个 RPC 保持现状，本 change 只覆盖 Agent 自主路径）。
- 不重写 OCR 批改引擎、配平算法、报告聚合逻辑（全部复用既有服务）。
- 不做学生档案（profile）注入改造——`student_profile` 由 ContextManager 注入，本 change 只接长期存储读写。
- 不实现家长报告/辅导的前端页面（工具经 SSE `_component/_route` 交前端消费）。
- 不为辅导工具新增专门的向量检索知识库——复用 `ToolContext.search`（web_search 客户端）做联网补充。

## Decisions

### D1. 辅导组：6 专题工厂复用 + 3 独立工具

6 个苏格拉底专题工具由同一工厂 `_make_topic_tutor(topic, method_steps)` 生成（doc 30 §8.2）：每工具构造自己的 system prompt（四步法步骤字典），输入统一 `(equation, question, student_input)`，输出引导式对话，不直接给答案。独立实现：`chemistry_tutor`（按 `ctx.user.role` 分流 800 字教研/500 字引导，中文关键词命中路由）、`simulate_experiment`（LLM 生成实验报告八段）、`balance_equation`（`check_balance`/`count_elements` 确定性配平，LLM 仅做讲解）。

**替代方案**：6 个独立手写实现 → 冗余且随方法迭代难维护；工厂 + 方法字典最贴合 doc 30 §8.2。`balance_equation` 曾考虑复用 `tools_exam.generate_questions` 的四维审核，但配平是核心且已被 equation.py 确定性覆盖，直接复用即可。

**persona 暴露决策**：注册全部 6 个专题工具；persona 白名单按 doc 30 §4.2 配置（student 工具集列 4 专题：ionic/stoichiometry/redox/equilibrium，未列 periodic_law/organic——§3.4 表与 §4.2 存在差异）。设计选择：工具注册即存在，可见性由 persona YAML 决定；`periodic_law_tutor` / `organic_tutor` 注册但默认不在 student persona，如需启用由后续 persona 调整（属配置，不属本 change 规格）。

### D2. OCR 组：薄封装 ocr/batch.py

- `query_ocr_progress` → `aggregate_tasks(db, get_session_or_404(db, batch_id))`（只读，聚合完成/失败/等待百分比 + 每张状态）。
- `grade_answer_sheets` → `run_grading(...)`（LLM 批量批改，中间结果挂 `task.result['grading']` 并推进会话到 grading，commit 持久化；不写 StudentAnswer——save_grading_results 是唯一落作答记录的入口，返回批改汇总 + 逐题判定）。
- `save_grading_results` → `save_grading_results(...)`（逐学生校验学号 `_student_by_no` 后写 StudentAnswer + 触发障碍诊断，返回保存数量 + 诊断触发确认）。
- 身份校验：`ctx.user.role == "teacher"`，非教师返回 ForbiddenError（与 doc 30 §3.5 一致）。

**审批门控**：`grade_answer_sheets` 与 `save_grading_results` 均加入 `APPROVAL_TOOLS`。理由：grade 批量调用 LLM（高成本）且是 save 的前置；save 写库并触发诊断（副作用）。`query_ocr_progress` 只读不审批。

### D3. 记忆组：读路径接线 + 写路径补接线

- `memory_student_get(student_id)` → `ctx.memory.long_term.student_diagnosis_history(student_id)`（最近 5 条）+ `ctx.db.get(Student, student_id).learning_plan`；读取门控按角色：student 仅可读自身（`ctx.user.role == "student"` 时校验 Account.role_id == student_id，跨生返回 ForbiddenError）、parent 仅可读已绑定子女（StudentParentBinding active）。
- `memory_teacher_get(teacher_id)` → `ctx.memory.long_term.teacher_pref(teacher_id)`；仅 teacher。
- **写接线**：`push_student_diagnosis` 在诊断完成点调用——① `diagnose_barrier` 个体诊断落库后；② OCR `save_grading_results` 写库触发诊断后。写入 best-effort（LongTermStore.put 内部静默），失败不阻塞主流程。不接写路径则读工具恒空，违反 spec「诊断写记忆」场景，故必须接。

### D4. 家长报告组：生成不发送 + 发送需审批

- `generate_parent_report(student_id)` → 复用 `weekly_report_service.generate_weekly_report` / `report_service.build_student_report` 聚合数据，返回家长可读报告预览文本 + `requires_confirmation: true` 标记，不发送。
- `send_report_to_parent(student_id, report_data)` → 复用 `send_learning_plan` 的通知模式：查 `StudentParentBinding(status=active)` → 逐绑定家长写 `ParentNotification(notification_type=weekly_report)` → commit → 返回发送确认 + 已通知家长数。
- 两者 `ctx.user.role == "teacher"` 校验，parent 角色调用返回 ForbiddenError（家长门控沿用诊断组「家长班级诊断受限」语义，但报告工具是 teacher 专属，parent 无报告权限）。
- `send_report_to_parent` 加入 `APPROVAL_TOOLS`：外部家长可见通知，属高风险写操作。`send_learning_plan` 未审批是历史决策，不影响本工具按 spec 审批。

### D5. TOOL_META 与 persona 白名单更新

- `tool_meta.py` 新增 16 条 TOOL_META：限次按 doc 30 §3.4–3.7（专题 5 / chemistry_tutor 3 / simulate_experiment 2 / balance_equation 3；OCR 3/2/2；记忆 1/1；报告 5/3）+ 角色 + 中文描述。
- `registry.py` 新增 4 个 import 块 + 16 条 `register_impl`。
- `personas/*.yaml` 白名单补齐（student 4 专题 + chemistry_tutor + simulate_experiment；tutor chemistry_tutor + simulate_experiment + balance_equation；teacher 增 OCR 3 + memory_teacher_get + 报告 2 + balance_equation）。现状 personas 已预列部分未注册工具（build_langgraph_tools 会告警丢弃），本 change 即将其接线。

### D6. Guard PREREQUISITES 扩展（可选 L1）

为关键参数补 L1 前置校验：`query_ocr_progress`/`grade_answer_sheets` 需 batch_id 非空、`save_grading_results` 需 batch_id、`memory_student_get` 需 student_id、`send_report_to_parent` 需 student_id。keyword 匹配，缺失返回 `missing_prerequisites`。

## Risks / Trade-offs

- [§3.4 表与 §4.2 学生 persona 差异（periodic_law/organic）] → 按 §4.2 配置白名单，两工具注册但默认不暴露；若产品要求学生可用，仅改 persona YAML，不改规格。
- [grade/save 审批增加教师操作摩擦] → 审批门控只拦 Agent 自主调用，教师确认后恢复；与 delete_bank/自适应确认端点同一交互范式，代价可控。
- [记忆写接线依赖诊断完成点可靠触发] → 写 best-effort 静默失败不阻塞；读端对空历史返回空列表而非报错（LongTermStore.get 默认值）。
- [send_report_to_parent 与 send_learning_plan 审批不一致] → 明确新写工具按 spec 审批，历史工具不追改，避免扩大本 change 面。
- [辅导工具依赖 LLM 讲解质量] → 苏格拉底引导在系统 prompt 固化步骤；balance_equation 配平结果由确定性算法兜底，LLM 只做讲解放大。

## Migration Plan

- 纯新增，无 schema 变更、无既有契约修改；分 4 个工具组独立提交（辅导 → OCR → 记忆 → 家长报告），每组自带 unit + 集成测试。
- 回滚：`git revert` 单个工具组提交即可，Guard `APPROVAL_TOOLS` 与 persona YAML 随之回退，不影响既有 14 工具。

## Open Questions

无——工具输入/角色/限次由 doc 30 §3.4–3.7 与既有 TOOL_META 模式完全确定。
