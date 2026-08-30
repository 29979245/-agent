## Context

补齐三类能力。动机与范围见 proposal.md - Why。当前状态与约束：

- **工具注册**：`app/agents/tools/tool_meta.py` 用 `register_tool(ToolMeta)` 声明式注册，`TOOL_IMPLS`/`TOOL_SCHEMAS`/`TOOL_META` 三表由 `registry.py` 维护，工具实现按域拆分（tools_exam/tools_diagnosis/...）。当前 30 工具。`app/evals/agent_tools.py` 断言 `registry_total_is_30` 与 student 精确工具集。
- **浏览器**：`requirements.txt` 已含 `playwright>=1.40`；无任何浏览器工具/会话管理代码。doc 30 §3.8 要求单实例 + 60s 空闲回收 + 无头 Chromium。
- **GuardState**（`app/agents/guard.py`）：请求级 dataclass。L1 参数前置（PREREQUISITES dict）、L2 每工具计数（call_limits + record_call）、L3 执行键 `(tool, sorted_args)` 请求级生命周期去重（D11）、L4 审批（mark_approved/check_approval）。无会话/认证、无并发、无超时窗口。
- **MCP**：本会话先按 1B 收编再按用户决定还原，代码/端点/测试/规格/ADR 已恢复，17 用例通过。本变更不再改动 MCP。
- **遗留**：`openspec/changes/retire-mcp-http-layer/` 变更目录已作废（决策被反转），需移除。

## Goals / Non-Goals

**Goals:**

- 落地 doc 30 §3.8 浏览器 5 工具（单实例生命周期、全角色可用），工具总数 30 → 35，e2e eval 同步。
- GuardState 按课程验收点加固：L1 会话/认证上下文校验、L2 Token Bucket + 并发在途上限、L3 去重超时窗口——保持既有 30 工具与审批/去重语义向后兼容（D11 登记时机不变）。
- 移除作废的 retire-mcp-http-layer 变更目录。

**Non-Goals:**

- 不改动 MCP 实现（已还原，仅验证）。
- 不实现 doc 30 §5 之外未要求的 Guard 特性（如跨请求共享限流、分布式锁）。
- 不改 Agent 工具路径（registry execute_tool 调用顺序与 SSE 事件协议）。
- 不引入 Playwright 之外的浏览器依赖。

## Decisions

- **浏览器工具实现：新文件 `tools_browser.py` + 模块级 `BrowserSession` 管理器**。
  - `BrowserSession`：进程内单例，惰性启动 `async_playwright().chromium.launch(headless=True)`；每次操作更新时间戳；`get_or_start()` 前检查空闲 >60s 则 `close()` 重启（doc 30 §3.8）。操作间共享同一 `context/page`。
  - 5 个工具为 async 函数（`async def browse_navigate(ctx, url, ...)`），经 registry 的 `execute_tool` 异步执行（既有工具已支持 async，见 tools_diagnosis）。返回 dict 结构匹配 doc 30 §3.8（标题+文本/元素文本/跳转前后 URL/清空输入/Base64 截图）。
  - 截图返回 Base64 由工具层直接编码，不新增 SSE 事件类型。
  - **备选**：pyppeteer/requests-html 被否——playwright 已在依赖且支持 headless Chromium 与选择器 API，与 doc 30 §3.8 吻合。
- **浏览器工具注册与 Persona 接线**：`tool_meta.py` 追加 5 条 `register_tool(ToolMeta(..., personas=PERSONAS, call_limit=3, icon="🌐"))`；4 个 Persona YAML `available_skills` 追加 `browse_navigate/browse_read/browse_click/browse_input/browse_screenshot`。`effective_skills` 交集逻辑不变（浏览器工具进 TOOL_META 后自动进入各 Persona 交集）。`agent_tools.py` eval 更新：`registry_total_is_30` → 35、student 精确工具集 +5 浏览器、新增「全角色含浏览器」检查。
- **GuardState L1 会话/认证上下文校验**：`check_prerequisites` 之后新增 `check_context(ctx)`——校验 ToolContext 含 user 且 `user_id` 与 `role` 均非空（规格「缺 user 身份/角色」）；缺失返回 `missing_prerequisites`（复用错误码，不新增码，避免破坏 LLM 错误处理契约）。ToolContext 注入点（agent.py/registry）保证既有调用路径自动获得该检查。
- **GuardState L2 Token Bucket + 并发**：`call_limits` 语义保留（每轮总次数），另加 `token_bucket`（每工具每秒补充速率 + 容量）与 `max_concurrent`（默认 1，TOOL_META 可配置）。`check_limit` 依次校验桶令牌与在途数；`record_call` 扣桶令牌；执行开始 `_in_flight += 1`、结束 `-= 1`（在 `execute_tool` 的 try/finally 中挂钩）。并发超限仍返回 `limit_exceeded`。**备选**：仅计数限流保持现状——被否，课程验收点明确要求 Token Bucket 与并发。
- **GuardState L3 去重超时窗口**：`_execution_keys` 改为 `{key: registered_at_monotonic}`；`is_duplicate` 在 `now - registered_at < dedup_window_s`（默认 60s）内判重，过期视为可重试。D11 登记时机（审批通过后、执行开始时）不变；窗口默认值放入 GuardState 构造参数，agent.py 可不改。
- **作废变更清理**：删除 `openspec/changes/retire-mcp-http-layer/`（无未提交代码依赖；代码改动已全部还原）。

## Risks / Trade-offs

- [浏览器工具引入 Playwright 运行时开销与浏览器进程] → 单实例 + 60s 空闲回收；单测 mock `BrowserSession`，不启动真实浏览器；playwright 已在依赖。
- [L2 Token Bucket 并发挂钩 execute_tool 增加复杂度] → 挂钩点集中（try/finally），对既有单线程/低并发路径无行为变化；超限仍返回既有 error_code。
- [L3 窗口过期后副作用工具可重放] → 窗口仅影响「相同调用去重」，审批工具仍受 L4 门控；D11 防重放语义保留（窗口内仍去重）。
- [课程验收点超出 doc 30 §5] → 已在 proposal/spec 明确标注为强化项；doc 30 未矛盾，仅未细化算法。

## Migration Plan

1. `tools_browser.py` + `BrowserSession`（含 mock 单测）。
2. `tool_meta.py` 注册 5 工具；Persona YAML 白名单追加；`agent_tools.py` eval 更新。
3. `guard.py` L1/L2/L3 扩展；`execute_tool` 并发挂钩；扩展 `test_agent_guard.py`。
4. 全量 pytest + `run_evals --tier all --compare baseline.json`（工具计数 30→35 更新基线）。
5. 删除 `openspec/changes/retire-mcp-http-layer/`；`graphify update .`。
