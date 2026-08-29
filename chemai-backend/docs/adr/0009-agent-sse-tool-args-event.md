# Agent SSE 事件协议发射 `tool_args` 流式参数事件（按 doc 41，偏离 doc 30 §13.1）

Agent 阶段（phase-6）决策：SSE 适配器发射 **12 种事件**，在 doc 30 §13.1 的 11 种（phase/tool_call/tool_result/text/component/navigate/populate/action/exam_images/error/done）基础上**新增 `tool_args`**（doc 41 §3.3 的流式参数 delta 事件），工具调用序列为 `tool_call(preparing) → tool_args(delta×N) → tool_result(completed/failed)`。前端 api.js 增加按 toolCallId 累积 delta 的缓冲。

**Why**：
1. **doc 41 与 doc 30 事件表冲突**——doc 41 §3.3 定义 `tool_call` + `tool_args`（参数流式 delta），doc 30 §13.1 事件表无 tool_args。两者同属设计文档但渲染层文档（41）对工具参数流式呈现有明确规格，经用户拍板按 doc 41。
2. **UX 价值**——用户在工具卡片上能看到参数"逐段填充"（preparing 骨架 → 参数渐显 → executing），比整块 tool_call 更真实地反馈 Agent 执行过程。
3. **偏离 doc 30 需记录**——若不记录，后人按 doc 30 §13.1 核对事件表会认为 tool_args 是多余事件而删除，破坏 doc 41 的渲染契约。

**调和方式**：`app/agents/sse_adapter.py` 从 LangGraph `astream_events` 的 `on_chat_model_stream` 抓取 `tool_call_chunks` 参数增量，聚合成 `{type:"tool_args", toolCallId, delta}` 事件；模型客户端开启流式工具调用模式。若某 Provider 不流式返回工具参数，**降级为单次 `tool_call`（完整参数内联）→ 直接 `tool_result`**，前端对空 tool_args 缓冲跳过，兼容两种形态。

**后果**：适配器复杂度略增（参数增量聚合 + 降级分支）；前端 tool_call 卡片的 preparing→executing 两态渲染需要 tool_args 缓冲；不影响事件协议的其余 11 种与前端已解析子集（text/phase/tool_call/tool_result/done/error）。
