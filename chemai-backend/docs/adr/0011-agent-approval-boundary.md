# Agent 审批边界：Guard L4 仅限自主路径，审批恢复为重开流（非 interrupt）

Agent 阶段（phase-6）决策（2026-08-29 code-review 补记）：审批/审计边界固化，防止后人按 CLAUDE.md「审批类操作必须走 GuardState 第 4 层」一刀切误判。

## 决策 1：GuardState L4 审批门控只覆盖 Agent 自主工具路径

- **Agent 路径**（`run_agent_chat` → `tools/registry.py execute_tool`）：LLM 自主调用的审批类工具（assign_adaptive_practice / delete_bank）必须走 Guard 第 4 层审批门控。
- **MCP 路径**（`app/api/v1/mcp.py` → `mcp/registry.py` → `mcp/tools.py`）：16 个 MCP 工具的写操作（create_training / submit_training / complete_review / send_notification / trigger_warning_check 等）**只做角色门控，不走 Guard L4**。

**Why**：审批门控的存在意义是**防 LLM 自主性失控**——LLM 可能擅自给学生发计划、删题库，所以需要人在环。MCP 是明确意图的 RPC 端点，调用方是集成或人类，其调用本身就是审批；套 Guard L4 会引入无主审批卡死（awaiting_approval 无人可点）而没有任何安全增益。

**How to apply**：MCP 写工具的角色门控（`_user` 校验）保持为唯一授权边界；若未来 MCP 面向不可信调用方开放，再按需把指定工具接入 Guard L4。

## 决策 2：审批恢复 = 重开新 SSE 流 + 指令注入，非 LangGraph interrupt

审批恢复端点（`/api/agent/approval/resume`）的语义：`pop_pending_approval` 取出待审批项 → 重开一条新 `run_agent_chat` 流，注入一条审批指令消息（「请立即调用该工具完成操作」/「请停止该操作」）→ 以 `guard.mark_approved(tool, args)` 预标记，使 LLM 复调同一工具时 L4 放行。

**不是** LangGraph `interrupt` 真 checkpoint 恢复（D1 曾提及 interrupt 开箱即用，但 design D13 拍板重开流方案）。

**Why**：重开流复用同一 `{subject}:{thread_id}` 检查点键，对话历史经 ContextManager 裁剪后完整续上，LLM 有完整上下文。相对 interrupt 代价低、与现有 checkpointer 拓扑（ADR-0010 单写者）无冲突。

**接受的风险**：被批工具的执行依赖 LLM 按指令重调——若 LLM 不重调，被批工具永不执行。缓解：指令是强指令，MiMo/qwen 按指令重调成功率很高；失败用户可见，可重发。`mark_approved` 精确匹配 `(tool, sorted_args)`，LLM 用不同参数重调会重新进入审批（D11 防重放语义，行为正确）。

## 决策 3：MCP 写工具不走 Guard L4 审批，但仍落审计日志

MCP 路径不做 Guard L4 审批（决策 1），但每次 MCP 工具执行**仍记录 JSONL 审计**（`call_mcp_tool` 内调 `audit_logger.log`，persona=调用者角色）。审批与审计是正交的两条边界：审批防「LLM 自主性失控」需要人在环；审计是对所有工具执行的追责，覆盖 MCP 这类明确意图的 RPC 同样成立。角色越权 / 参数校验失败发生在执行前，不产生审计条目。

## 后果

- MCP 写工具不产生 `awaiting_approval` 暂停流，调用方阻塞式等待结果。
- 审批恢复不依赖进程存活（pending 注册表为进程内 dict，见 ADR-0010 单实例约束）。
- 若未来要「被批工具必定执行」的强保证，迁移路径：改用 LangGraph `interrupt` + 真检查点恢复（需核对 ADR-0010 单写者约束），本阶段不实现。
