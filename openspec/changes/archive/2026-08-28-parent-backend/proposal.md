> **Scope**: parent（课程 53，Git 分支 phase-4/business）

## Why

家长端是 ChemAI 三端中的低频高价值触点。数据模型（Parent/StudentParentBinding/ParentNotification）与家长登录、预警/每日通知的写入链路已存在，但家长侧**读路径缺失**：没有绑定/解绑、子女列表、子女报告、周报、通知列表与已读等 API，家长角色目前无法以"仅见绑定子女"的隔离方式消费数据。同时周报生成（LLM 通俗化）与通知类型模型尚未落地。设计文档 33 定义了完整家长端，本 change 实现其后端服务（前端页面下一轮）。

## What Changes

- **新增 `parent-portal` 能力**：家长端全部读/写 API——
  - 绑定流程：`POST /api/parent/bind`（绑定码→建绑定，复用 inactive 历史行）、`DELETE /api/parent/bind/{binding_id}`（软删置 inactive）
  - `GET /api/parent/children`：已绑定子女列表
  - `GET /api/parent/child/{student_id}/report`：子女报告（家长视角聚合，通俗字段，不含 bind_code/教师评语）
  - `GET /api/parent/child/{student_id}/weekly`：本周周报（懒生成）
  - `POST /api/parent/child/{student_id}/weekly/generate`：手动生成周报（周内去重）
  - `POST /api/parent/child/{student_id}/report/ai-summary`：AI 通俗解读（一次性 LLM）
  - `GET /api/parent/notifications` + `PUT /api/parent/notifications/{id}/read`：通知列表/已读
- **新增周报生成服务 `weekly_report_service.py`**：复用 `FallbackDiagnosisLLMClient` + `extract_json`，按文档 33 §7 prompt 模板生成 `{summary, detail, advice, no_data}`，术语转换表嵌入 system prompt。
- **数据权限守卫**：新增 `require_parent`（role=parent）与 `require_bound_child(student_id)`（active 绑定校验）依赖，挂在全部家长数据端点；通知端点 parent_id 取自 token。
- **数据模型变更**：
  - `NotificationType` 枚举 3 类 → 文档 5 类（weekly_report/score_alert/learning_plan/reminder/daily_report），迁移映射 message→daily_report、warning→score_alert、report→weekly_report
  - `ParentNotification` 补 `created_at` 时间戳列（index）
  - `Student` 补 `weekly_report`(JSON) + `weekly_report_week`(date) 列
- **既有发送方同步**：`daily.py`、`early_warning.py` 改用新枚举值。
- **调度**：APScheduler 加周一 08:00 UTC 周报生成 job（遍历有 active 绑定家长的学生）。
- **Webhook 事件系统**（设计文档 33 §10.3/§10.4 系统内可用部分）：Webhook 注册/列表/取消 + 手动触发 + HMAC-SHA256 签名投递，7 种事件类型（practice.assigned/practice.completed/exam.created/exam.graded/warning.triggered/student.login/review.due）；在既有发送点接入实际触发（预警→warning.triggered、每日练习→practice.assigned）。
- **LMS 配置与同步 API**：`GET/POST /api/integration/lms/config`（集成配置存取）、`POST /api/integration/lms/sync`（外部连接器就绪前为状态占位）。
- **BREAKING**：`NotificationType` 枚举值替换，依赖旧值（message/warning/report）的 API 消费方需同步更新。

## Capabilities

### New Capabilities
- `parent-portal`: 家长端后端门户——绑定/解绑、子女列表、子女报告、周报生成与查询、通知列表/已读、AI 通俗解读，含数据权限守卫（require_parent / require_bound_child）
- `lms-integration`: Webhook 事件系统与 LMS 配置/同步 API——7 种事件类型注册/列表/取消/手动触发、HMAC-SHA256 签名投递、集成配置存取（外部连接器按文档 33 §10 不在版本范围）

### Modified Capabilities
- `data-models`: `NotificationType` 枚举升级为文档 5 类；`ParentNotification` 增 `created_at`；`Student` 增 `weekly_report`/`weekly_report_week` 列
- `access-control`: 家长最小可见数据边界需求从"推迟"落地——家长数据端点一律经 active 绑定校验，未绑定返回 403"未绑定该学生"，学生不存在返回 404
- `early-warning`: 预警通知类型引用由 `warning` 改为 `score_alert`（随枚举升级）

## Impact

- **代码**：`app/api/v1/parent.py`（新建路由）、`app/api/v1/integration.py`（新建 Webhook/LMS 配置路由）、`app/services/parent_service.py`（家长聚合）、`app/services/weekly_report_service.py`（周报 LLM）、`app/services/integration/webhook_service.py`（签名投递）、`app/core/parent_auth.py`（守卫依赖）、`app/db/models/parent.py`（created_at）、`app/db/models/enums.py`（NotificationType）、`app/db/models/integration.py`（WebhookRegistration）、`app/db/models/org.py`（Student 周报列）、`app/db/session.py`（scheduler job）
- **迁移**：1 个 Alembic migration（枚举迁移 + created_at + Student 列 + WebhookRegistration 表）
- **既有模块**：`daily.py`/`early_warning.py` 通知类型值改新枚举并接入 webhook emit；`report_service.py` 底层 helper 被复用不改
- **不涉及**：钉钉/企微/LTI/SFTP 外部连接器——按文档 33 §10 不在当前版本范围；前端页面（parent-login.html/parent.html）下一轮 change
