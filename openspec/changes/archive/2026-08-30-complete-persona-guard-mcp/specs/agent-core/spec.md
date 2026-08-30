## ADDED Requirements

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

## MODIFIED Requirements

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
