# agent-core Specification

## Purpose
Agent 对话核心能力：将用户自然语言经意图分类与 Persona 过滤送入 LangGraph 单 Agent ReAct 循环执行工具，通过 SSE 事件流推送执行过程与结构化结果，并附带 Provider 多路回退、Guard 四层护栏、审计日志、对话状态持久化、审批门控与 MCP 工具服务器。

## Requirements

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
系统 SHALL 对每次工具调用执行四层检查：第 1 层前置条件（必填参数校验与**会话/认证上下文校验**——ToolContext 缺 user 身份/角色时返回 `missing_prerequisites`，参数缺失时亦返回该码）；第 2 层调用限次（每工具每轮 call_limit，采用 **Token Bucket 算法**并以**并发在途数上限**约束同时执行的工具数，超限返回 `limit_exceeded`）；第 3 层去重（以工具名+排序参数为标识，执行键在审批通过后、工具开始执行时登记，重复返回 `dedup_skipped`；**执行键带超时窗口，窗口过期后相同调用可再次执行**；审批阻塞/前置失败未启动执行则不登记）；第 4 层审批门控（approval 工具未确认返回 `requires_approval_blocked`）。各层失败 SHALL 返回结构化错误，LLM 可基于错误继续（提示用户补全/基于已有结果/跳过）。

#### Scenario: 前置条件缺失
- **WHEN** 调用缺少必填参数（如 search_exam_bank 的 keyword ≤2 字符）或 ToolContext 缺 user 身份/角色
- **THEN** 返回 missing_prerequisites，LLM 向用户询问补全

#### Scenario: 调用超限
- **WHEN** 某工具本轮调用超过 call_limit，或并发在途数超过上限，或 Token Bucket 令牌耗尽
- **THEN** 返回 limit_exceeded，LLM 基于已有结果继续

#### Scenario: 相同调用去重
- **WHEN** 已执行过的相同 tool+args 在去重窗口内再次调用
- **THEN** 返回 dedup_skipped，跳过执行

#### Scenario: 去重窗口过期后可重试
- **WHEN** 相同 tool+args 的去重窗口已过期
- **THEN** 该调用不再判重，可正常执行

#### Scenario: 未审批工具被拦截
- **WHEN** 审批工具（如 assign_adaptive_practice）未获确认即被调用
- **THEN** 返回 requires_approval_blocked，提示先请求审批

### Requirement: Persona 工具过滤
系统 SHALL 提供 4 个 Persona（teacher/student/tutor/parent），每个定义 system_prompt 与 available_skills 白名单；Agent 工具集 SHALL 取"Persona YAML 白名单"与"TOOL_META 注册的该 Persona 可用工具"的交集；所有 Persona SHALL 自动包含浏览器工具（browse_navigate/browse_read/browse_click/browse_input/browse_screenshot）；parent Persona SHALL 通过 data_access 限制可见数据项（can_see/cannot_see）。

#### Scenario: 白名单交集过滤
- **WHEN** 以 teacher Persona 创建 Agent
- **THEN** 工具集为 teacher YAML available_skills 与 TOOL_META teacher 注册集合的交集，且自动含 5 个浏览器工具

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
系统 SHALL 提供出题组 7 个工具：`search_exam_bank`、`web_search`、`show_exam_workbench`、`generate_questions`、`save_to_bank`、`list_banks`、`delete_bank`；各工具 SHALL 遵循 doc 30 §3.2 的输入/限次/角色约束，限次定义于 TOOL_META；出题组依赖联网的工具 SHALL 通过 `ToolContext.search` 注入独立搜索客户端（与 LLM Provider 解耦）。

`search_exam_bank` SHALL 提供三级搜索：本地关键词→向量召回→联网补齐；SHALL 支持可选的 `source / region / knowledge_point` 结构化过滤；ChromaDB 向量命中 SHALL 按相似度 ≥ 0.6 入列（低于阈值不入列），ChromaDB 不可用降级为关键词匹配的命中无相似度、不适用阈值（doc 25 §7.4）；本地结果不足 3 条且含 keyword 时 SHALL 触发联网补齐，结果标记"AI辅助搜索（本地题库仅 N 道，以下为AI补充）"；命中含图片字段（`image_url`）的题目时 SHALL 发射 `exam_images` SSE 事件携带图片 URL；题目图片字段由题目数据源（导入/上传）提供，数据源未携带图片字段时该事件不触发。

`web_search` SHALL 执行多路联网搜索并将结果摘要至 400 字内；搜索服务未配置时 SHALL 返回明确降级提示（如"未配置"），不得抛错或中断对话。

`show_exam_workbench` SHALL 内联渲染出题工作台面板，返回参数 SHALL 采用 `types:[{val, active, qty}]` 数组结构（val 为题型值、active 为是否激活、qty 为生成数量），并携带 knowledge_points / difficulty / source / bank_name。

`generate_questions` SHALL 依次执行 RAG 检索→LLM 生成→化学式标准化→完整四维审核；化学式标准化 SHALL 将裸化学式（如 H2O、NaCl）数字下标转换为 LaTeX 并包装为 `$...$`，箭头统一替换为 `\rightarrow` / `\rightleftharpoons`；四维审核 SHALL 覆盖方程配平、反应条件、产物、分子结构，每维返回判定（passed/warning/failed/blocked）与 evidence；仅系数配平失败（blocked）SHALL 判定不可保存，条件/产物/结构问题 SHALL 标记为 warning 不阻断保存（软标记，doc 26 §6.3）；生成题目 SHALL 携带陷阱提示 `trap_hint` 与 RAG 元数据 `is_from_rag / source_question_id / similarity / match_method`；提示词 SHALL 按检索真题数分支——命中 ≥3 条走模式 A（以真题为风格参考）、否则模式 B（纯生成）。

`save_to_bank` SHALL 创建题库文件夹、逐题入库并同步向量索引；成功返回 SHALL 携带三段式跳转协议 `{navigate, page:"exam-v2", populate, actions:[{action:"openTab", payload:"bank"}]}`，其中 populate 含 set_id / set_name。

`list_banks` SHALL 列出题库文件夹，返回 id / name / question_count / is_preset。

`delete_bank` SHALL 删除题库文件夹及其题目关联，题目实体保留，需审批。

#### Scenario: 三级搜索兜底标记
- **WHEN** 本地题库结果不足 3 条且含 keyword
- **THEN** 触发联网补齐，结果标记"AI辅助搜索（本地题库仅 N 道，以下为AI补充）"

#### Scenario: 化学式标准化
- **WHEN** generate_questions 生成含裸化学式（如 H2O、NaCl）的题目
- **THEN** 数字下标转换为 LaTeX 格式并包装为 $...$，箭头统一替换为 \rightarrow / \rightleftharpoons

#### Scenario: 删除题库需审批
- **WHEN** Agent 调用 delete_bank
- **THEN** 进入审批流程，未确认不执行删除

#### Scenario: 联网搜索未配置降级
- **WHEN** 搜索客户端未配置（ToolContext.search 为空）而 Agent 调用 web_search 或三级搜索的 Tier-3
- **THEN** 返回明确降级提示（"未配置"），对话正常继续，不抛错

#### Scenario: 向量阈值过滤
- **WHEN** 向量召回结果的相似度低于 0.6
- **THEN** 该结果不入列，不计入可用真题数

#### Scenario: 四维审核阻断
- **WHEN** 生成的题目系数配平失败（blocked）
- **THEN** 该题标记为不可保存并给出 balance 维度 evidence，不得带病入库；条件/产物/结构问题软标记 warning 不阻断（doc 26 §6.3）

#### Scenario: RAG 元数据回传
- **WHEN** generate_questions 基于检索到的真题生成题目
- **THEN** 返回题目携带 is_from_rag / source_question_id / similarity / match_method 元数据

#### Scenario: 工作台 types 数组
- **WHEN** Agent 调用 show_exam_workbench
- **THEN** 返回含 types 数组（val/active/qty）的面板参数，前端按数组渲染多题型生成

#### Scenario: 保存三段式跳转
- **WHEN** save_to_bank 成功入库
- **THEN** 返回 navigate(page="exam-v2") + populate(含 set_id/set_name) + actions(openTab=bank) 三段协议

#### Scenario: exam_images 事件
- **WHEN** search_exam_bank 命中的题目含图片字段（image_url）
- **THEN** 发射 exam_images SSE 事件携带图片 URL，供前端渲染；题目数据源未携带图片字段时不触发

### Requirement: 工具切片——诊断组
系统 SHALL 提供诊断组 7 个工具：`diagnose_barrier`（个体或班级两级诊断，返回三维障碍分布与主导类型）、`show_diagnosis`（内联渲染诊断图表面板）、`show_students`（三模式：无班级列出班级/有班级学生卡片/有过滤按障碍筛选）、`weekly_report`（LLM 生成 200 字自然语言周报，通俗不制造焦虑）、`assign_adaptive_practice`（为班级生成个性化 ZPD 练习预览，不落库；教师确认后由前端调用 API 持久化）、`generate_learning_plan`（跳转学生管理页触发学习方案生成）、`send_learning_plan`（持久化学习计划并通知学生）；诊断工具 SHALL 支持智能名称解析（纯数字 ID 或中文姓名模糊匹配）与班级名灵活匹配（中文数字→阿拉伯数字）。家长角色 SHALL 仅可访问已绑定子女的学情（doc 30 §4.2）：携带班级参数调用班级级诊断 SHALL 返回 ForbiddenError，班级统计分布仅 teacher 角色可获取。

#### Scenario: 个体诊断
- **WHEN** 提供 student_id 或姓名调用 diagnose_barrier
- **THEN** 返回三维障碍分布与主导类型

#### Scenario: 智能名称解析多结果
- **WHEN** 中文姓名模糊匹配命中多个学生
- **THEN** 返回候选列表供选择

#### Scenario: 自适应练习预览不落库
- **WHEN** Agent 调用 assign_adaptive_practice
- **THEN** 返回每生预览（ZPD 难度 / 难度档位 / 主导障碍 / 知识点 / 选中题目引用），不创建练习记录、不复制题目入库；教师确认后由前端调用 REST API 持久化

#### Scenario: 布置练习需审批
- **WHEN** 教师在前端确认自适应练习预览并提交批量
- **THEN** 由确认落库 API 审批后逐生创建练习记录；未经教师确认不创建任何练习记录（审批门控从工具移至 API 确认端点，doc 28 §六）

#### Scenario: 家长班级诊断受限
- **WHEN** parent 角色携带 class_id/class_name 调用 diagnose_barrier
- **THEN** 返回 ForbiddenError（家长仅支持个体诊断），不返回任何班级学生画像

### Requirement: 工具切片——辅导组
系统 SHALL 提供辅导组 9 个工具：6 个苏格拉底式专题辅导（`ionic_equation_tutor`、`stoichiometry_tutor`、`redox_tutor`、`equilibrium_tutor`、`periodic_law_tutor`、`organic_tutor`）、`chemistry_tutor`、`simulate_experiment`、`balance_equation`；各工具 SHALL 遵循 doc 30 §3.4 的输入/限次/角色约束（专题辅导限次 5 仅学生、chemistry_tutor 限次 3 全体、simulate_experiment 限次 2 学生与辅导、balance_equation 限次 3 辅导与教师），限次定义于 TOOL_META。

6 个专题辅导工具 SHALL 由同一工厂函数生成（doc 30 §8.2），按各自方法分步引导而非直接给答案：离子方程式（判断可拆物质→写成离子→删不变离子→检查守恒）、化学计量（提取已知量→选公式→列关系式→分步计算）、氧化还原（标化合价→找升降→电子守恒配平）、化学平衡（分析平衡体系→勒夏特列原理→三段式计算）、周期律（位置→结构→性质推断）、有机推断（逆合成分析+官能团转化）；输入 SHALL 含方程式、题目与学生输入。

`chemistry_tutor` SHALL 接收问题、学生水平与角色：teacher 角色 SHALL 输出 800 字教研分析（考点分布+教学策略+学生误区），student/tutor 角色 SHALL 输出 500 字引导教学；中文关键词（什么是/怎么做/原理）命中 SHALL 路由至本工具。

`simulate_experiment` SHALL 接收实验名称，由 LLM 生成实验报告：目的、仪器、步骤、现象、方程式、原理、安全提醒、考点。

`balance_equation` SHALL 接收方程式，用确定性算法完成配平并执行四维审核，返回两侧各元素原子计数与配平结果，不依赖 LLM 配平。

#### Scenario: 专题苏格拉底引导
- **WHEN** 学生请求某专题辅导（如离子方程式）
- **THEN** 按该专题方法分步引导，不直接给出答案

#### Scenario: 通用辅导角色分流
- **WHEN** teacher 角色调用 chemistry_tutor
- **THEN** 输出 800 字教研分析；student 角色调用则输出 500 字引导教学

#### Scenario: 配平确定性零误差
- **WHEN** 调用 balance_equation 配平方程式
- **THEN** 返回两侧各元素原子计数相等且结果确定，不依赖 LLM 配平

#### Scenario: 模拟实验报告
- **WHEN** 调用 simulate_experiment
- **THEN** 返回含目的、仪器、步骤、现象、方程式、原理、安全提醒、考点的实验报告

### Requirement: 工具切片——OCR批改组
系统 SHALL 提供 OCR 批改组 3 个工具：`query_ocr_progress`、`grade_answer_sheets`、`save_grading_results`；各工具 SHALL 遵循 doc 30 §3.5 的角色约束（仅教师，限次 3/2/2 定义于 TOOL_META）；写工具 `grade_answer_sheets` 与 `save_grading_results` SHALL 纳入 Guard 第 4 层审批门控，未经确认不得执行。

`query_ocr_progress` SHALL 接收教师 ID 与批次 ID，按批次聚合 OCR 任务进度（完成/失败/等待百分比），返回批次摘要与每张状态，为只读工具。

`grade_answer_sheets` SHALL 接收教师 ID、批次 ID 与考试 ID，对已完成 OCR 的任务批量执行 LLM 批改，返回批改汇总与逐题判定；SHALL 只计算不落库，写库由 `save_grading_results` 承担。

`save_grading_results` SHALL 接收教师 ID 与批次 ID，逐学生校验学号已注册后写入作答记录并自动触发障碍诊断，返回保存数量与诊断触发确认。

#### Scenario: 批次进度聚合
- **WHEN** 教师调用 query_ocr_progress 提供教师 ID 与批次 ID
- **THEN** 返回完成/失败/等待百分比与每张状态

#### Scenario: 批量批改不落库
- **WHEN** 调用 grade_answer_sheets 对已完成 OCR 任务批量批改
- **THEN** 返回批改汇总与逐题判定，不写入任何作答记录

#### Scenario: 批改与保存需审批
- **WHEN** Agent 调用 grade_answer_sheets 或 save_grading_results
- **THEN** 进入审批流程，未确认不执行批改或写库

#### Scenario: 保存触发诊断
- **WHEN** 保存批改结果
- **THEN** 逐学生写入作答记录并触发障碍诊断，返回保存数量与诊断触发确认

### Requirement: 工具切片——记忆组
系统 SHALL 提供记忆组 2 个工具：`memory_student_get`（读取学生诊断历史最近 5 条与当前学习计划，全体角色可用）、`memory_teacher_get`（读取教师偏好设置，仅教师）；读取 SHALL 来自长期存储 `LongTermStore`；障碍诊断完成 SHALL 触发学生记忆写入（`push_student_diagnosis`），使读取有数据可依。

`memory_student_get` SHALL 按学生 ID 与记忆类型读取长期存储中的诊断历史（最近 5 条）与当前学习计划，返回诊断历史与学习计划；student 角色 SHALL 仅可读取自身记忆，跨学生访问返回 ForbiddenError。

`memory_teacher_get` SHALL 读取教师偏好设置（教学风格、难度偏好、班级配置），返回教师配置数据；仅 teacher 角色可调用。

记忆写入 SHALL 在障碍诊断完成时触发：诊断结果（含 LLM 不可用时降级规则诊断）SHALL 写入该学生长期存储。

#### Scenario: 学生读自身记忆
- **WHEN** student 角色读取自身诊断历史与学习计划
- **THEN** 返回最近 5 条诊断历史与当前学习计划

#### Scenario: 跨学生读取受限
- **WHEN** student 角色读取其他学生记忆
- **THEN** 返回 ForbiddenError

#### Scenario: 教师读偏好
- **WHEN** teacher 角色调用 memory_teacher_get
- **THEN** 返回教学风格、难度偏好、班级配置数据

#### Scenario: 诊断写记忆
- **WHEN** 障碍诊断完成（含 LLM 不可用降级为规则诊断）
- **THEN** 诊断结果写入学生长期存储，后续可被 memory_student_get 读取

### Requirement: 工具切片——家长报告组
系统 SHALL 提供家长报告组 2 个工具：`generate_parent_report`（生成报告预览，不发送）与 `send_report_to_parent`（推送周报到已绑定家长，发送需审批）；两者 SHALL 仅 teacher 角色可调用（doc 30 §3.7）；parent 角色 SHALL 不得生成或发送家长报告。

`generate_parent_report` SHALL 接收学生姓名与 ID，聚合练习、诊断、知识点数据，生成家长可读报告预览文本与需确认标记，不发送给家长。

`send_report_to_parent` SHALL 接收学生 ID 与报告数据，推送周报到已绑定家长的通知列表，返回发送确认；SHALL 纳入 Guard 第 4 层审批门控，未经确认不推送。

#### Scenario: 生成报告预览
- **WHEN** teacher 调用 generate_parent_report
- **THEN** 返回家长可读报告预览与需确认标记，不发送

#### Scenario: 发送需审批
- **WHEN** Agent 调用 send_report_to_parent
- **THEN** 进入审批流程，未确认不推送

#### Scenario: 发送确认
- **WHEN** 教师确认发送
- **THEN** 推送周报到已绑定家长的通知列表，返回发送确认

#### Scenario: 家长角色受限
- **WHEN** parent 角色调用 generate_parent_report 或 send_report_to_parent
- **THEN** 返回 ForbiddenError，不生成或发送任何报告

### Requirement: 浏览器工具组
系统 SHALL 提供 5 个浏览器工具：`browse_navigate`（打开 URL 等待加载，返回页面标题与 ≤8000 字文本）、`browse_read`（按元素选择器提取页面元素文本，≤8000 字符）、`browse_click`（点击元素并等待 0.5s，返回跳转前后 URL）、`browse_input`（清空并填入输入框文本）、`browse_screenshot`（截取指定区域 PNG 并以 Base64 返回）；所有 Persona SHALL 自动包含这 5 个浏览器工具（`personas=PERSONAS`）；浏览器 SHALL 维护单一实例，60s 无操作空闲超时自动回收，按需重新启动无头浏览器（doc 30 §3.8）。

#### Scenario: 打开并读取网页
- **WHEN** Agent 调用 browse_navigate 携带目标 URL
- **THEN** 打开页面等待加载，返回标题与文本；随后 browse_read 按选择器提取元素内容

#### Scenario: 点击与输入
- **WHEN** Agent 调用 browse_click / browse_input 携带元素选择器
- **THEN** 点击返回跳转前后 URL；输入先清空再填入，均复用同一页面实例

#### Scenario: 截图
- **WHEN** Agent 调用 browse_screenshot 携带区域选择器
- **THEN** 返回 Base64 编码的 PNG 截图

#### Scenario: 空闲回收
- **WHEN** 浏览器实例 60s 无操作
- **THEN** 自动关闭实例，下次调用重新启动
