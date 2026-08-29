## Purpose

Agent 对话核心能力：将用户自然语言经意图分类与 Persona 过滤送入 LangGraph 单 Agent ReAct 循环执行工具，通过 SSE 事件流推送执行过程与结构化结果，并附带 Provider 多路回退、Guard 四层护栏、审计日志、对话状态持久化、审批门控与 MCP 工具服务器。

## ADDED Requirements

### Requirement: SSE 对话流端点
系统 SHALL 提供 `POST /api/agent/chat/langgraph/stream` 端点，接收 JSON 体（含 `message` 用户消息、`thread_id` 对话标识、`context` 上下文对象），返回 `text/event-stream` 流式响应；`thread_id` SHALL 由客户端提供且后端不生成；该端点 SHALL 要求 JWT 认证（`/api/agent/` 不在认证白名单）；`version` 参数 SHALL 默认 `"v2"`，为 `"v1"` 时 SHALL 返回明确错误（v1 回退未实现），不静默回落 v2。

#### Scenario: 正常对话返回事件流
- **WHEN** 已认证客户端携带 message、thread_id 调用该端点
- **THEN** 返回 `text/event-stream`，依次推送 phase / tool_call / tool_args / tool_result / text / done 事件

#### Scenario: 未认证被拒绝
- **WHEN** 无有效 Bearer token 调用该端点
- **THEN** 返回 401，不建立 SSE 流

#### Scenario: 指定 v1 版本
- **WHEN** 请求携带 version="v1"
- **THEN** 返回明确错误提示 v1 回退未实现，不返回 v2 行为

#### Scenario: 前端断连取消
- **WHEN** 客户端中断请求
- **THEN** 后端停止 Agent 执行并清理该请求资源

### Requirement: Gateway 意图分类
系统 SHALL 在请求进入 Agent 前进行意图分类：优先 LLM 语义分类，失败时降级到约 20 组中文关键词匹配，结果合并去重后输出；分类为 `chat` 时进入 ReAct 循环，`navigate` 时走快捷路径——跳过 Agent 引擎，直接依次推送 `navigate`（含 page 与 params）、`done` 与结束帧；分类结果 SHALL 返回 type（chat/navigate）、最多 3 个建议工具 tools、可选 page；涉及图片/拍照/OCR/识别/上传的消息 SHALL 使用视觉模型 Provider 分类，其余使用通用推理模型。

#### Scenario: chat 意图进入 ReAct
- **WHEN** 用户消息被分类为 chat
- **THEN** 请求进入 ReAct 循环并推送完整事件流

#### Scenario: navigate 意图走快捷路径
- **WHEN** 用户消息被分类为 navigate 且指定目标页面
- **THEN** 跳过 Agent 引擎，直接推送 navigate 事件（page+params）、done 与结束帧

#### Scenario: LLM 分类失败降级关键词
- **WHEN** LLM 分类调用失败或返回格式异常
- **THEN** 使用关键词兜底匹配，LLM 结果优先级高于关键词结果

### Requirement: 单 Agent ReAct 循环
系统 SHALL 使用 LangGraph 单 Agent（`create_react_agent`）执行 `chat` 意图：全量（Persona 过滤后）工具注入，LLM 通过工具描述自主选择；每对话 SHALL 有独立状态与检查点；递归上限 SHALL 为 12；递归耗尽时 SHALL 推送用户友好的 error（"处理超时，Agent 重试次数用尽"）与结束帧。

#### Scenario: LLM 自主选择工具
- **WHEN** 用户请求可被工具满足
- **THEN** LLM 自主选择匹配工具并触发执行

#### Scenario: 递归耗尽
- **WHEN** ReAct 循环达到递归上限仍未结束
- **THEN** 推送 user 友好 error 事件与 done 事件

### Requirement: SSE 事件协议
系统 SHALL 通过 SSE 适配器推送 12 种事件：`phase`、`tool_call`、`tool_args`、`tool_result`、`text`、`component`、`navigate`、`populate`、`action`、`exam_images`、`error`、`done`；`phase` SHALL 覆盖 5 种状态（thinking/executing/planning/reply/awaiting_approval）；工具调用 SHALL 依次发射 `tool_call`（含 name 与 tool 分类）、`tool_args`（流式参数 delta，含 toolCallId 与 delta）、`tool_result`（含 success 与 result）；LLM 生成的文本 SHALL 通过 `text` 事件逐段推送；工具返回中的内联面板指令（`_component`）与页面跳转指令（`_route`）SHALL 在 Guard 层剥离，分别转为 `component`/`populate`/`navigate`/`action` 事件，LLM 收到的仅纯净业务结果。

#### Scenario: 工具调用三阶段事件
- **WHEN** Agent 执行一个工具
- **THEN** 依次推送 tool_call（preparing）、若干 tool_args（参数增量）、tool_result（completed/failed）

#### Scenario: 内联面板与跳转剥离
- **WHEN** 工具返回含 _component 或 _route 字段
- **THEN** 该字段不进入 LLM 上下文，转发射 component/navigate 等前端事件

#### Scenario: 文本流式推送
- **WHEN** LLM 生成回复文本
- **THEN** 通过 text 事件分段推送，最终以 done 事件结束

### Requirement: Guard 四层护栏
系统 SHALL 对每次工具调用执行四层检查：第 1 层前置条件（必填参数校验，缺失时返回 `missing_prerequisites`）；第 2 层调用限次（每工具每轮 call_limit，超限返回 `limit_exceeded`）；第 3 层去重（以工具名+排序参数为标识，执行键在审批通过后、工具开始执行时登记，重复返回 `dedup_skipped`；审批阻塞/前置失败未启动执行则不登记）；第 4 层审批门控（approval 工具未确认返回 `requires_approval_blocked`）。各层失败 SHALL 返回结构化错误，LLM 可基于错误继续（提示用户补全/基于已有结果/跳过）。

#### Scenario: 前置条件缺失
- **WHEN** 调用缺少必填参数（如 search_exam_bank 的 keyword ≤2 字符）
- **THEN** 返回 missing_prerequisites，LLM 向用户询问补全

#### Scenario: 调用超限
- **WHEN** 某工具本轮调用超过 call_limit
- **THEN** 返回 limit_exceeded，LLM 基于已有结果继续

#### Scenario: 相同调用去重
- **WHEN** 已执行过的相同 tool+args 再次调用
- **THEN** 返回 dedup_skipped，跳过执行

#### Scenario: 未审批工具被拦截
- **WHEN** 审批工具（如 assign_adaptive_practice）未获确认即被调用
- **THEN** 返回 requires_approval_blocked，提示先请求审批

### Requirement: Persona 工具过滤
系统 SHALL 提供 4 个 Persona（teacher/student/tutor/parent），每个定义 system_prompt 与 available_skills 白名单；Agent 工具集 SHALL 取"Persona YAML 白名单"与"TOOL_META 注册的该 Persona 可用工具"的交集；所有 Persona SHALL 自动包含浏览器工具（本切片浏览器组未实现，白名单暂不含 browse_*）；parent Persona SHALL 通过 data_access 限制可见数据项（can_see/cannot_see）。

#### Scenario: 白名单交集过滤
- **WHEN** 以 teacher Persona 创建 Agent
- **THEN** 工具集为 teacher YAML available_skills 与 TOOL_META teacher 注册集合的交集

#### Scenario: 白名单外工具不可见
- **WHEN** 某工具不在当前 Persona 白名单中
- **THEN** 该工具不出现在 Agent 工具描述中，LLM 无法选择

### Requirement: Provider 多路回退
系统 SHALL 维护三级 Provider 回退链：MiMo-V2.5（主）→ 通义 qwen-turbo → DeepSeek-V4-Flash（兜底），每级 SHALL 最多重试 3 次且指数退避（1s→2s→4s）；主 Provider 三次失败后 SHALL 按链切换下级；每 Provider SHALL 维护进程内熔断器（连续失败 ≥3 次熔断 30s，熔断期间请求 SHALL 直接短路至下一级，冷却后 SHALL 自动恢复）；`.env` 的 `LLM_PROVIDER` SHALL 为 `mimo`；未识别的 Provider 标识 SHALL 触发启动异常。视觉/图片消息 SHALL 使用具备视觉能力的 Provider（MiMo）。

#### Scenario: 主 Provider 限流降级
- **WHEN** MiMo 返回限流且重试耗尽
- **THEN** 请求自动切换到 qwen-turbo，再失败切 DeepSeek

#### Scenario: 全部 Provider 失败
- **WHEN** 三级 Provider 全部失败
- **THEN** 向上抛错并由调用方处理，SSE 层推送 error 事件

### Requirement: 审计日志
系统 SHALL 以 JSONL 格式记录每次技能执行，字段含 timestamp、persona、skill_name、args（已脱敏）、result_summary（截断至 200 字符）、duration_ms、error（可选）；敏感字段集合 SHALL 为 password、phone、parent_phone、token、api_key、secret，命中替换为 `***`；内存 SHALL 维护最近 100 条环形缓冲；写入 SHALL 为 best-effort，任何异常被静默捕获不阻塞主流程。

#### Scenario: 技能执行写入审计
- **WHEN** 任一工具执行完成
- **THEN** 该次执行追加写入 JSONL 审计文件并进入环形缓冲

#### Scenario: 敏感参数脱敏
- **WHEN** 工具参数含 api_key 等敏感字段
- **THEN** 审计记录中该字段值为 "***"

#### Scenario: 写入失败不阻塞
- **WHEN** 审计文件不可写（磁盘满/权限不足）
- **THEN** 主流程继续，异常被静默捕获

### Requirement: 上下文三层裁剪
系统 SHALL 在对话消息超过 30 条时执行裁剪：无条件保留最近 6 条；更早消息中含教学关键词（学生/诊断/障碍/考试/题目/知识点/分数/薄弱/学习计划/错题/成绩/练习/班级/教师）的额外保留；被丢弃消息 ≥10 条时 SHALL 调用 LLM 压缩为 ≤200 字中文摘要（失败则直接丢弃）；裁剪后消息结构为"摘要→关键词命中历史→最近 6 条→当前输入"，写回对话检查点。

#### Scenario: 未达阈值不裁剪
- **WHEN** 消息数 ≤30
- **THEN** 跳过裁剪直接组装

#### Scenario: 超阈值三层裁剪
- **WHEN** 消息数 >30 且被丢弃消息 ≥10
- **THEN** 生成摘要，保留关键词命中与最近 6 条，写回检查点

### Requirement: 对话状态持久化
系统 SHALL 通过 LangGraph Checkpointer 将对话状态持久化到 SQLite，服务重启后不丢失；检查点键 SHALL 以 JWT 主体为命名空间（`{JWT 主体, thread_id}`），前端 `context.user_id` 与 JWT 主体不一致 SHALL 返回 403；每个 `thread_id` SHALL 对应独立检查点，互不干扰；审批流程中 Agent SHALL 暂停并可从检查点恢复执行；重置操作 SHALL 将检查点消息清空。

#### Scenario: 多轮保持
- **WHEN** 同一 thread_id 后续请求到达
- **THEN** 从上次检查点恢复对话上下文

#### Scenario: 对话隔离
- **WHEN** 不同 thread_id 并发对话
- **THEN** 各自状态互不影响

#### Scenario: 审批中断恢复
- **WHEN** 审批工具等待教师确认后收到恢复请求
- **THEN** Agent 从暂停检查点恢复执行

### Requirement: 审批流程
系统 SHALL 对审批工具（assign_adaptive_practice、delete_bank）在调用时推送 `awaiting_approval` 阶段并暂停执行；awaiting_approval 后 SHALL 不发 `done` 而直接关闭流，前端 SHALL 据此识别暂停（区别于断连）并锁定输入、展示确认/取消卡片；系统 SHALL 提供恢复执行端点，教师确认后 SHALL 从检查点恢复并重开一条新 SSE 流续推；取消时 SHALL 让 Agent 收到拒绝信号并调整行为。

#### Scenario: 等待审批暂停
- **WHEN** Agent 调用审批工具
- **THEN** 推送 awaiting_approval 阶段事件，执行暂停

#### Scenario: 确认后恢复
- **WHEN** 教师点击确认并调用恢复端点
- **THEN** Agent 从暂停点恢复，继续执行该工具

#### Scenario: 取消审批
- **WHEN** 教师点击取消
- **THEN** Agent 收到拒绝信号，改用其他方式继续对话

### Requirement: MCP 工具服务器
系统 SHALL 提供 MCP 路由前缀 `/api/mcp`，含三个端点：`GET /api/mcp/tools`（列出全部 MCP 工具及其参数 Schema）、`POST /api/mcp/call`（通用调用，请求体含 tool 与 arguments）、`POST /api/mcp/tools/{name}`（按名称直接调用）；SHALL 通过工具装饰器注册 16 个 MCP 工具（ocr_recognize、generate_questions、generate_variant、get_wrong_questions、create_training、submit_training、get_review_tasks、complete_review、get_class_overview、get_student_stats、trigger_warning_check、get_pending_warnings、send_notification、diagnose_question、get_barrier_distribution、get_knowledge_heatmap）；未注册工具名 SHALL 返回明确错误。

#### Scenario: 列出工具
- **WHEN** GET /api/mcp/tools
- **THEN** 返回 16 个工具的名称与参数 Schema

#### Scenario: 按名称调用
- **WHEN** POST /api/mcp/tools/ocr_recognize 携带合法参数
- **THEN** 执行对应工具并返回结果

#### Scenario: 未注册工具
- **WHEN** 调用不存在的 MCP 工具名
- **THEN** 返回明确错误，不静默忽略

### Requirement: 工具切片——出题组
系统 SHALL 提供出题组 7 个工具：`search_exam_bank`（三级搜索：本地关键词→向量召回→联网补齐，结果不足 3 条时标记"AI辅助搜索"）、`web_search`（多路联网搜索，结果摘要至 400 字内；后端使用独立搜索 API、与 LLM Provider 解耦）、`show_exam_workbench`（内联渲染出题工作台面板）、`generate_questions`（RAG 检索→生成→化学式标准化→四维审核，LaTeX 箭头与裸化学式标准化）、`save_to_bank`（创建题库文件夹、逐题入库、同步向量索引）、`list_banks`（列出题库文件夹）、`delete_bank`（删除题库文件夹及题目关联，题目实体保留，需审批）；各工具 SHALL 遵循 doc 30 §3.2 的输入/限次/角色约束，限次定义于 TOOL_META。

#### Scenario: 三级搜索兜底标记
- **WHEN** 本地题库结果不足 3 条且含 keyword
- **THEN** 触发联网补齐，结果标记"AI辅助搜索（本地题库仅 N 道，以下为AI补充）"

#### Scenario: 化学式标准化
- **WHEN** generate_questions 生成含裸化学式（如 H2O、NaCl）的题目
- **THEN** 数字下标转换为 LaTeX 格式并包装为 $...$，箭头统一替换为 \rightarrow / \rightleftharpoons

#### Scenario: 删除题库需审批
- **WHEN** Agent 调用 delete_bank
- **THEN** 进入审批流程，未确认不执行删除

### Requirement: 工具切片——诊断组
系统 SHALL 提供诊断组 7 个工具：`diagnose_barrier`（个体或班级两级诊断，返回三维障碍分布与主导类型）、`show_diagnosis`（内联渲染诊断图表面板）、`show_students`（三模式：无班级列出班级/有班级学生卡片/有过滤按障碍筛选）、`weekly_report`（LLM 生成 200 字自然语言周报，通俗不制造焦虑）、`assign_adaptive_practice`（为班级生成个性化 ZPD 练习，需审批）、`generate_learning_plan`（跳转学生管理页触发学习方案生成）、`send_learning_plan`（持久化学习计划并通知学生）；诊断工具 SHALL 支持智能名称解析（纯数字 ID 或中文姓名模糊匹配）与班级名灵活匹配（中文数字→阿拉伯数字）。

#### Scenario: 个体诊断
- **WHEN** 提供 student_id 或姓名调用 diagnose_barrier
- **THEN** 返回三维障碍分布与主导类型

#### Scenario: 智能名称解析多结果
- **WHEN** 中文姓名模糊匹配命中多个学生
- **THEN** 返回候选列表供选择

#### Scenario: 布置练习需审批
- **WHEN** Agent 调用 assign_adaptive_practice
- **THEN** 进入审批流程，未确认不执行布置
