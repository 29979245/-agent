## Context

后端 `app/agents/` 是空壳（4 个空 Persona YAML + 6 个空包），`/api/agent` 路由不存在。前端已具备完整 SSE 消费端（`api.js` `agentChatStream` 解析 text/phase/tool_call/tool_result/done/error），是唯一契约客户。底层业务服务（diagnosis/question/ocr/exercise/analytics/audit/integration）与依赖（langgraph/langchain-openai/chromadb/playwright）均已就绪。`.env` 当前 `LLM_PROVIDER=deepseek`。

## Goals / Non-Goals

**Goals:**
- 打通"前端 → JWT → Gateway → ReAct v2 → SSE"垂直闭环，让 ai-tutor 从 404 变成真对话
- 铺 slice-1 工具（诊断 7 + 出题 7，含 web_search），全部经 Guard 四层 + TOOL_META + Persona 过滤
- Provider 三级回退 + 审计 + 对话持久化 + 审批 + MCP + 前端增量（tool_args/富渲染器/审批卡片）

**Non-Goals:**
- Planner、长期记忆层（AsyncSqliteStore）、浏览器工具组（browse_*×5）、v1 多智能体回退——均不在本 change
- 对话 CRUD 完整端点（list/history/new/delete/reset）——前端无消费者，仅 stream 端点 + checkpointer
- 不做生产级限流策略（Token Bucket 参数从简，可后调）

## Decisions

### D1: ReAct 用 LangGraph `create_react_agent`（非自研循环）
单 Agent v2，全量（Persona 过滤后）工具注入，LLM 自选。checkpointer（AsyncSqliteSaver）持久化、`astream_events` 做 SSE、`interrupt` 做审批恢复全部开箱即用。
- **备选**：自研 Thought→Action→Obs 循环（可控但需自建持久化/审批/SSE，2-3 个月 ROI 不匹配，doc 38 §3.2 明言放弃）；CrewAI/AutoGen（偏 Agent 间对话，不适合工具调用准确率目标）

### D2: Provider 回退链 MiMo→qwen→deepseek（偏离 .env 现状）
按 doc 38 §2.3 名义顺序。`.env` 的 `LLM_PROVIDER` 从 `deepseek` 改为 `mimo`。MiMo 是唯一同时具备视觉+联网+化学满分（20/20）的模型，放主位后 doc 30 §6.2 的"图片走视觉模型"能力路由被主链路吸收，Gateway 的 provider 选择逻辑简化。
- **备选**：deepseek 主（实测最快 2.1s 最低成本，但无视觉/联网，doc 38 §2.3 名义顺序不符）——被用户否决
- **偏离记录**：这是对 `.env` 现状的刻意偏离，需 ADR，否则后人会把 `LLM_PROVIDER` 改回 deepseek

### D3: SSE 发射 12 事件含 `tool_args`（按 doc 41，偏离 doc 30 §13.1）
doc 41 §3.3 要求 `tool_call`→`tool_args`(流式 delta)→`tool_result`，doc 30 §13.1 事件表无 tool_args。用户拍板按 doc 41。实现需从 `astream_events` 的 `on_chat_model_stream` 抓取 `tool_call_chunks` 参数增量，模型客户端开启流式工具调用。前端 api.js 增加按 toolCallId 累积 delta 的缓冲。
- **偏离记录**：偏离 doc 30 §13.1 事件表，需 ADR
- **降级兜底**：若某 Provider 不流式返回工具参数，退化为单次 `tool_call`（完整参数内联）+ 直接 `tool_result`，前端兼容（tool_args 为空则跳过缓冲）

### D4: v1 不建，`version` 参数门
`version` 默认 `"v2"`；`"v1"` 返回 501 明确错误。等 v2 稳定、确有降级需求再补，届时共享基建（GuardState/checkpointer/TOOL_META）已就位。
- **备选**：slice-1 同步建 v1（双份 Agent+SSE 适配器，双倍维护）——被否决

### D5: 审批走"门控 + 恢复端点 + 前端卡片"（方案 A）
doc 30 §5.2 第 4 层门控 assign_adaptive_practice/delete_bank；后端建恢复执行端点；前端 api.js 补审批提交调用 + ai-tutor/parent.html 补审批卡片（awaiting_approval → 确认/取消 → 调恢复端点）。
- **备选**：B（只建后端，前端后置）——会导致审批工具一调就卡死无人可点，被否决

### D6: 鉴权收紧 + 限流
`middleware.py` 白名单移除 `/api/agent/`，强制 JWT（前端已带 Bearer）。限流用 Token Bucket 挂在 stream 端点级（参数从简，可后调）。

### D7: 目录布局（`app/agents/`）
```
app/agents/
├── gateway.py          # 意图分类 (LLM+关键词, navigate 快捷路径)
├── guard.py            # GuardState 四层 + 特殊字段剥离
├── agent.py            # ReAct v2 工厂 (LangGraph) + version 门
├── sse_adapter.py      # astream_events → 12 事件
├── planner.py          # （本切片不建，占位不实现）
├── context.py          # 三层裁剪 + 消息组装
├── audit.py            # JSONL + 环形缓冲
├── factories/          # 模型工厂 / agent 工厂
├── tools/              # 14 个 slice-1 工具 + TOOL_META
├── mcp/                # MCP server (16 工具)
├── personas/           # 4 个 YAML 填充
└── chem_skills/        # 保留空包（底层实现后续切片填充）
```
`app/api/v1/agent.py` 新路由：`/api/agent/chat/langgraph/stream`、审批恢复、`/api/mcp/*`。

### D8: 工具接线（包装现有服务）
14 个 slice-1 工具映射：`diagnose_barrier`/`show_diagnosis`/`show_students`/`weekly_report`/`assign_adaptive_practice`/`generate_learning_plan`/`send_learning_plan` → `services/diagnosis`+`services/analytics`；`search_exam_bank`/`generate_questions`/`save_to_bank`/`list_banks`/`delete_bank`/`show_exam_workbench` → `services/question`+`services/audit`；`web_search` → **新增搜索集成**（见 Open Questions）。每个工具经 Guard 包装，`_component`/`_route` 在 Guard 层剥离。

### D9: 前端增量
`api.js` 加 `tool_args` 累积缓冲 + 审批提交方法；ai-tutor.html 加 `renderBarrierOverview`/`renderQuestionCards`/`renderStudentList` + 通用兜底渲染器 + 审批卡片。复用现有 `SA.renderMarkdown`/`SA.renderChem`/XSS 净化。

## Risks / Trade-offs

- [**tool_args 流式在 LangGraph 的复杂度**] → 用 `on_chat_model_stream` 聚合 tool_call_chunks；Provider 不流式则降级单次 tool_call（D3 兜底）
- [**MiMo 主位延迟 3.5s > deepseek 2.1s**] → 主位由 doc 38 名义顺序决定；若 P95 首 token 超 3s 目标，回退链前移 deepseek 是配置级改动
- [**MCP 扩大 slice-1 工作量**] → 11/16 个 MCP 工具映射已存在服务，纯包装层；generate_questions MCP 版走简化路径（无 RAG+audit）
- [**审批前端增量扩散**] → 限定 api.js 一个方法 + 两张卡片（ai-tutor/parent），不复用旧确认弹窗
- [**SSE 契约 doc 41 vs doc 30 漂移**] → 以 ADR 固化"发 tool_args 按 doc 41"，防止后人按 doc 30 删事件
- [**web_search 需新搜索后端**] → 用 MiMo 内置联网能力或轻量搜索 API；见 Open Questions
- [**`/api/agent/` 收紧鉴权**] → 前端已带 Bearer，无调用方破坏；家长端浮动 AI 同走 JWT

## Migration Plan

- **部署**：新增 `app/agents/*` + `app/api/v1/agent.py` 并在 `main.py` 挂路由；`middleware.py` 白名单移除 `/api/agent/`；`.env` 改 `LLM_PROVIDER=mimo` 并确保 `MIMO_API_KEY`/`QWEN_API_KEY` 已配
- **回滚**：移除路由挂载 + 恢复白名单 + `.env` 回 `deepseek`——三处可逆，无数据迁移
- **评测**：每工具组完成触发 L2 集成评测（doc 32）；基线录入 `app/evals/baseline.json`

## Open Questions

- ~~**web_search 后端**~~ → **已定（D15）**：独立搜索 API（百度/SerpAPI/必应），与 LLM Provider 解耦
- **Token Bucket 参数**：速率/突发量初值？从简起步，运营后调
- **MiMo 可用性**：`.env` 中 `MIMO_API_KEY` 是否已配置有效 key？需实现前确认，否则回退链实际不可用

## Review Deltas（plan-eng-review 2026-08-29）

`/plan-eng-review`（含 Codex outside voice）审后修正，全部经用户拍板：

- **D10 熔断器（补 D2）**：LLMClient 每 Provider 维护进程内熔断器——连续失败 ≥3 次熔断 30s，熔断期请求直接短路下一级；链内每级 3 次退避（1s→2s→4s）不变。偏离"只做文档级回退"的 grill 决定，需 ADR 附注。
- **D11 去重登记时机（修 Guard 第 3 层）**：执行键 `(tool, sorted_args)` 在**审批通过后、工具开始执行时**登记（非进入 Guard 时、非成功后）。审批阻塞未启动 → 不登记（防"批准后误判 dedup 假死"）；副作用工具中途报错 → 执行键已存在 → 重试跳过（防重放写）。一个时机兼顾 A6 与防重放。
- **D12 Provider 能力声明式标志（修 D3）**：`model_factory` provider 配置字典加 `capabilities.stream_tool_args`，SSE 适配器读标志选"流式三连 tool_call→tool_args→tool_result"或"单次 tool_call"分支，不做运行时探测。
- **D13 审批暂停语义（修 D5）**：awaiting_approval 后**不发 done**、直接关闭流；api.js 识别"最后 phase=awaiting_approval 且无 done"为暂停（非断连），前端锁输入挂审批卡片；恢复端点重开新流续推。
- **D14 thread 所有权（补 D6）**：checkpointer 键 = `{JWT 主体, thread_id}`；校验前端 `context.user_id` 与 JWT 主体一致，不一致 403；前端 thread_id 持久化（localStorage），修复 parent.html `thread_id: null`。
- **D15 web_search Provider 无关（定 D8 Open Question）**：web_search 走独立搜索 API，与 LLM Provider 解耦，fallback 链对工具层透明。
- **部署拓扑**：agent 检查点库 AsyncSqliteSaver 为单写者拓扑——部署固定单 worker/单实例；多实例需共享存储方案（文档化限制）。
- **Gateway 分类（确认 D3 原判）**：维持 LLM 优先 + 记已知延迟，Evals 首帧 P95 验证，超目标再启用关键词前置。
