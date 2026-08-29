## Why

前端（ai-tutor.html / parent.html 浮动 AI）已经实现了完整的 SSE 客户端契约（api.js `agentChatStream` 解析 text/phase/tool_call/tool_result/done/error），但后端 Agent 引擎完全空白：`/api/agent/chat/langgraph/stream` 返回 404，前端只能降级显示"AI 助教后端尚未上线"。产品设计文档 30（Agent 对话系统）、38（技术选型）、41（SSE 渲染策略）已定义完整架构，但 `app/agents/` 目录只有 4 个空壳 Persona YAML 和空包。本 change 交付 Agent 核心架构的垂直切片：打通"前端请求 → 认证 → Gateway → ReAct v2 → SSE → 前端"闭环，并铺上第一层可用的工具组。

## What Changes

- **新增** `/api/agent/chat/langgraph/stream` SSE 流式端点，对接前端已存在的 `agentChatStream` 契约
- **新增** Gateway 意图分类器：LLM 语义分类 + 关键词兜底，`chat` 进 ReAct、`navigate` 走快捷路径（直发 navigate 事件跳过引擎）
- **新增** ReAct v2 单 Agent：LangGraph `create_react_agent`，Persona 过滤工具集，每对话一实例（checkpointer 持久化）
- **新增** LLMClient：三级 Provider fallback **MiMo→qwen→deepseek**，每级 3 次指数退避重试（doc 38 §2.3）；`.env` `LLM_PROVIDER` 改为 `mimo`
- **新增** Guard 四层护栏：前置检查 → 调用限次 → 去重 → 审批门控（approval 工具需确认）
- **新增** TOOL_META 工具注册表 + 首个工具切片（诊断 7 + 出题 7，含 `web_search`）
- **新增** 4 个 Persona YAML 填充（system_prompt + available_skills 白名单）
- **新增** ContextManager：三层裁剪（摘要→关键词命中→最近6条→当前输入）+ 工作记忆
- **新增** AuditLogger：内存环形缓冲（100 条）+ JSONL 追加，不阻塞主流程
- **新增** MCP 工具服务器：16 个工具 + 3 端点（`/api/mcp/tools`、`/api/mcp/call`、`/api/mcp/tools/{name}`）
- **新增** SSE 适配器：按 doc 30 §13.1 + doc 41 §3.3 发 12 种事件（11 + `tool_args`）
- **新增** 审批流程：门控 + 恢复执行端点 + 前端审批卡片/提交调用（方案 A）
- **新增** 前端增量：`tool_args` 累积缓冲 + 3 个核心富渲染器（renderBarrierOverview / renderQuestionCards / renderStudentList）+ 通用兜底
- **新增** `version` 参数门：默认 `"v2"`，`"v1"` 返回明确 501（v1 回退暂不实现）
- **BREAKING** `/api/agent/` 移出认证白名单，强制 JWT（前端已带 Bearer header）

## Capabilities

### New Capabilities
- `agent-core`: Agent 对话核心——Gateway 意图分类、ReAct v2 单 Agent、SSE 事件流、Guard 护栏、TOOL_META、Persona、Provider fallback、审计、MCP、审批、对话状态持久化

### Modified Capabilities
（无——现有规格不含 Agent 行为，全部为新增）

## Impact

- **后端代码**：`app/agents/`（core/factories/tools/mcp/chem_skills 从空包填充）、`app/api/v1/agent.py`（新路由）、`app/core/middleware.py`（白名单移除 `/api/agent/`）
- **前端代码**：`frontend/pages/js/api.js`（tool_args 累积）、`frontend/pages/ai-tutor.html`、`frontend/pages/parent.html`（审批卡片 + 富渲染器）
- **配置**：`.env` `LLM_PROVIDER=mimo`（+ MIMO_API_KEY/QWEN_API_KEY）
- **依赖**：langgraph / langchain-openai（已装，无需新增）；web_search 需搜索 API 集成；浏览器工具组不在本切片
- **评测**：`app/evals/` 新增 agent 工具组 L2 集成评测 + 基线条目（doc 32：每工具组完成触发）
- **范围外**：Planner、长期记忆层（AsyncSqliteStore）、浏览器工具组（5 个 browse_*）、v1 多智能体回退
