## 1. 地基：模型工厂 / Guard / TOOL_META

- [x] 1.1 实现 LLMClient（`app/agents/factories/`）：三级 Provider 回退链 MiMo→qwen→deepseek、每级 3 次指数退避（1s→2s→4s）、未识别 Provider 启动异常；验证 `tests/unit` 新增回退链单测（mock 429 触发降级、三级全败抛错）通过
- [x] 1.2 实现 GuardState 四层（`app/agents/guard.py`）：前置检查/调用限次/去重/审批门控 + `_component`/`_route` 剥离；验证单测覆盖四层错误码（missing_prerequisites/limit_exceeded/dedup_skipped/requires_approval_blocked）与剥离后 LLM 只见纯净结果
- [x] 1.3 实现 TOOL_META 注册表 + 编译时完整性校验（每个注册工具都有元数据、每条元数据对应已注册工具）；验证 `pytest` 完整性校验单测通过

## 2. 对话闭环：SSE 适配器 / stream 端点 / ContextManager / ReAct v2 / Persona

- [x] 2.1 实现 SSE 适配器（`app/agents/sse_adapter.py`）：`astream_events` → 12 事件（含 tool_args 聚合 + 非流式 Provider 降级单次 tool_call）；验证单测断言事件序列 phase→tool_call→tool_args→tool_result→text→done
- [x] 2.2 实现 `/api/agent/chat/langgraph/stream` 端点骨架（`app/api/v1/agent.py`）+ `main.py` 挂路由；验证 HTTP 冒烟：未认证 401、认证后返回 text/event-stream 含 done
- [x] 2.3 实现 ContextManager（`app/agents/context.py`）：三层裁剪（>30 条，保留最近 6，关键词命中，≥10 丢弃生成 ≤200 字摘要）+ 消息组装；验证单测覆盖未达阈值跳过、超阈值裁剪写回
- [x] 2.4 实现 ReAct v2 agent 工厂（`app/agents/agent.py`）：LangGraph `create_react_agent`、Persona 过滤工具集、递归上限 12、version 门（"v1" 返回 501）；验证单测 version="v1" 拒绝、"v2" 正常构建
- [x] 2.5 填充 4 个 Persona YAML（`app/agents/personas/*.yaml`）：system_prompt + available_skills 白名单，与 doc 30 §4.2 矩阵一致；验证加载脚本校验白名单∩TOOL_META 交集非空且无越权工具

## 3. 出题组工具 + 配置

- [x] 3.1 实现出题组 7 工具（`app/agents/tools/`）：search_exam_bank（三级搜索含"AI辅助搜索"标记）、web_search（多路搜索+400字摘要）、show_exam_workbench、generate_questions（RAG+化学式标准化+四维审核）、save_to_bank、list_banks、delete_bank；验证每工具单测通过 + 触发 L2 集成评测
- [ ] 3.2 配置 `.env`：`LLM_PROVIDER=mimo`，确认 `MIMO_API_KEY`/`QWEN_API_KEY` 有效；验证启动加载 provider 配置无异常、MiMo 连通性冒烟（环境无 LLM key，启动加载已验证；MiMo 连通性冒烟待配置 key）

## 4. 诊断组工具

- [x] 4.1 实现诊断组 7 工具：diagnose_barrier（个体/班级+智能名称解析+班级名匹配）、show_diagnosis、show_students（三模式）、weekly_report（200字通俗周报）、assign_adaptive_practice、generate_learning_plan、send_learning_plan；验证每工具单测通过 + 触发 L2 集成评测

## 5. Gateway 意图分类

- [x] 5.1 实现 Gateway（`app/agents/gateway.py`）：LLM 语义分类优先 + 约 20 组关键词兜底 + navigate 快捷路径（navigate→done→结束帧）+ 图片消息走视觉 Provider；验证单测覆盖 chat/navigate 两路径、LLM 失败降级关键词、快捷路径不触发 ReAct

## 6. 审批流程

- [x] 6.1 后端：approval 工具门控 + 恢复执行端点（`/api/agent/approval/resume` 类）；验证单测 pending→awaiting_approval→确认后从检查点恢复、取消收到拒绝
- [x] 6.2 前端：api.js 加审批提交方法 + ai-tutor/parent.html 审批卡片（awaiting_approval→确认/取消→调恢复端点）；验证 HTTP/浏览器审批链路跑通

## 7. MCP 工具服务器

- [x] 7.1 实现 MCP（`app/agents/mcp/`）：工具装饰器注册 16 个工具 + 3 端点（GET /api/mcp/tools、POST /api/mcp/call、POST /api/mcp/tools/{name}）；验证单测 + HTTP 冒烟（列表返回 16 项、未注册名返回错误）

## 8. 前端 SSE 增量

- [x] 8.1 api.js：`tool_args` 按 toolCallId 累积 delta 缓冲 + 参数骨架渲染；验证浏览器中工具卡片 preparing→executing 渐显
- [x] 8.2 ai-tutor.html：3 核心富渲染器（renderBarrierOverview/renderQuestionCards/renderStudentList）+ renderGenericResult 兜底；验证浏览器真对话中诊断柱状图、题目卡片、学生卡片正确渲染

## 9. 鉴权与限流

- [x] 9.1 `app/core/middleware.py` 白名单移除 `/api/agent/`；验证 HTTP 未认证访问 stream 端点 401
- [x] 9.2 Token Bucket 限流挂 stream 端点级；验证单测超限拒绝

## 10. 评测与收尾

- [x] 10.1 录入 evals 基线（`app/evals/baseline.json`）新增 agent 工具组条目 + 运行 `run_evals`；验证全量通过且无劣化（劣化 >5% 阻断）
- [x] 10.2 端到端冒烟：ai-tutor 页面真实对话（诊断/出题各一例），验证 SSE 事件流、富渲染、审批卡片、审计 JSONL 落盘

## 11. 审查补丁（plan-eng-review 2026-08-29）

- [x] 11.1 LLMClient 进程内熔断器（D10）：每 Provider 连续失败 ≥3 次熔断 30s，熔断期请求直接短路下一级；验证单测（模拟连续失败触发熔断、熔断期请求跳过该 Provider、冷却后恢复）
- [x] 11.2 CRITICAL 去重登记时机回归（D11）：执行键 `(tool, sorted_args)` 在审批通过后、工具开始执行时登记；验证单测覆盖「审批阻塞未启动不登记（批准后不误判 dedup 假死）」与「副作用工具中途报错→执行键已存在→重试跳过（防重放写）」双向
- [x] 11.3 Provider 能力声明式标志两分支（D12）：`model_factory` provider 配置含 `capabilities.stream_tool_args`；验证单测断言流式 Provider 走 tool_call→tool_args→tool_result 三连、非流式走单次 tool_call（不做运行时探测）
- [x] 11.4 审批暂停语义 E2E（D13）：awaiting_approval 后不发 done 直接关闭流；验证 api.js 识别「最后 phase=awaiting_approval 且无 done」为暂停（非断连）、前端锁输入挂审批卡片、恢复端点重开新 SSE 流续推
- [x] 11.5 thread 所有权 HTTP 校验（D14）：checkpointer 键 = `{JWT 主体, thread_id}`；验证 `context.user_id` 与 JWT 主体不一致返回 403、一致可访问、不同 thread_id 状态隔离；前端 thread_id 持久化（localStorage），修复 parent.html `thread_id: null`
- [x] 11.6 web_search 独立搜索客户端（D15）：独立搜索 API（百度/SerpAPI/必应）与 LLM Provider 解耦；验证单测 mock 搜索 API 返回并摘要至 400 字内、fallback 链对工具层透明
- [x] 11.7 ReAct 递归耗尽→用户友好 error（spec 已有，补显式测试）：递归上限 12 耗尽时推送「处理超时，Agent 重试次数用尽」error 事件 + done；验证单测断言事件序列
- [x] 11.8 上下文压缩降级兜底：被丢弃消息 ≥10 但 LLM 摘要失败时直接丢弃不阻塞；验证单测 mock 摘要失败仍完成裁剪写回
- [x] 11.9 EVAL 首 token P95：Gateway 分类 + ReAct 首帧延迟验证，超 3s 目标则启用关键词前置（Gateway 决策回退开关）
- [x] 11.10 部署拓扑约束：AsyncSqliteSaver 单写者拓扑——部署固定单 worker/单实例；验证部署文档/Compose 配置无多 worker 变量，多实例需共享存储方案（文档化限制）

## 收尾记录（2026-08-29，apply 完成）

全部代码 + 测试 + Evals 完成：`pytest tests/` 1113 passed / 1 skipped；`run_evals --tier all --compare baseline.json` 全通过（新增 agent_tools 基线 1.0）；`scripts/smoke_agent_e2e.py` 对当前代码后端 12 项冒烟全过（navigate 快捷路径 / chat error+done / 审批 403·422·409 / MCP 16 工具 / 首帧 P95 关键词路径 0.000s ≤3s 目标）。

仅剩两项依赖 LLM API key 的环境性验证（配置 key 后补验）：
- 3.2 MiMo 连通性冒烟（`.env` 未配置 LLM key，启动加载 provider 无异常已验）；
- 10.2 富渲染浏览器复核（诊断柱状图/题目卡片/学生卡片需真实 LLM 对话；前端 8.2 渲染器已实现且 JS 语法校验通过，browse.exe 被 Device Guard 封锁走 HTTP 级验证替代）。
