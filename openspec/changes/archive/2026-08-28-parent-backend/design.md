## Context

家长端数据模型（`Parent`/`StudentParentBinding`/`ParentNotification`，`app/db/models/parent.py`）、家长登录（`auth.py:221` parent_router `/login`，phone+bind_code 无密码）、学生端绑定码端点（`student.py:33`，31 字符集写 `Student.bind_code`）、预警/每日通知写入链路（`early_warning.py:258`、`daily.py:106`）均已存在并有测试。缺的是家长侧**读路径 API** 与**周报生成服务**。关键约束：parent 角色不入 RBAC 矩阵（`permissions.py:5` default-deny），家长端点不能走 `require_permission`；现有 LLM 基建为 `FallbackDiagnosisLLMClient.complete`（三级降级）+ `extract_json`（`llm_diagnosis.py`）；调度器 APScheduler 已用于每日练习/预警/OCR。动机见 proposal.md，行为契约见 specs。

## Goals / Non-Goals

**Goals:**
- 家长端全部 API（绑定/解绑、子女列表、子女报告、周报、通知、AI 解读）
- 周报 LLM 生成服务（文档 33 §7 通俗化）
- 通知模型升级（5 类型 + created_at + Student 周报列）
- 数据权限守卫（require_parent + require_bound_child）全端点覆盖

**Non-Goals:**
- 前端页面（parent-login.html/parent.html）——下一轮 change
- LMS 连接器（钉钉/企微/LTI/SFTP）与 Webhook——文档 33 §10 声明不在版本范围
- SSE 悬浮 AI 助手与 parent.yaml persona 填充——留待 agent 阶段
- 家长密码注册、直接搜索绑定——不制造越权路径

## Decisions

### D1 家长端点守卫：require_parent + require_bound_child（不走 RBAC 矩阵）
parent 不入矩阵（F2 default-deny），`require_permission` 会 403。新建 `app/core/parent_auth.py`：
- `require_parent`：解码 token → 断言 `role=="parent"` → 经 `Account.role_id` 解析 parent.id → `request.state.parent_id`。
- `require_bound_child(student_id)`：校验存在 `parent_id`+`student_id`+`status=active` 的绑定；无则 403"未绑定该学生"；学生记录不存在则 404。
- 通知类端点家长身份只取 token，不信任 query 参数。
- **备选**：把 parent 塞进矩阵。否决——F2 设计意图就是家长走独立路径，塞矩阵会污染五角色权威矩阵且无法表达"绑定行级"语义。

### D2 绑定生命周期：软删 + 复用历史行
`DELETE /api/parent/bind/{id}` 置 `status=inactive`（D8 不删记录）。`POST /api/parent/bind` 逻辑：学生存在且码匹配 `Student.bind_code` → 已有 active 绑定返回 400 → 存在 inactive 历史行则复用（update relation/bind_code/status）→ 否则新建。`(parent_id, student_id)` 唯一约束因此继续成立。绑定成功后置空 `Student.bind_code`（消费一次性码）。
- **备选**：删约束允许多历史行。否决——丢绑定可追溯性，查询要到处过滤 active。

### D3 家长报告：新建 parent_service.py，复用 report_service 底层 helper
不直接复用 `build_student_report` 返回结构——它含家长不可见字段（`bind_code`、`teacher_comment`）。`parent_service.py` 调用 `report_service` 的 `_practice_records`/`_accuracy`/`_streak_days`/`_build_knowledge_points`（同模块私有函数），组装家长形态：4 统计卡 + 知识掌握概览 + 通俗学习特点 + 本周动态时间线。响应模型显式不含 bind_code/排名/教师评语。

### D4 NotificationType 升级 5 类 + 数据迁移
枚举 `report/warning/message` → `weekly_report/score_alert/learning_plan/reminder/daily_report`。迁移映射存量行：`message→daily_report`、`warning→score_alert`、`report→weekly_report`。同步改 `daily.py`（message→daily_report）与 `early_warning.py`（warning→score_alert）的发送值。`learning_plan`/`reminder`/`weekly_report` 本阶段先有类型，发送方后续接入。

### D5 ParentNotification 补 created_at
沿袭现有模式（`warning.py:42`）：`created_at: DateTime(default=datetime.utcnow, index=True)`，支撑通知列表时间倒序分页。

### D6 周报存储：挂 Student 列 + 按周去重
`Student.weekly_report`(JSON) + `weekly_report_week`(date)。沿袭 `learning_plan` 存生成物的既有模式；按学生键天然服务多绑定家长。去重：`weekly_report_week == 本周周一` 则返回缓存，否则生成并覆写。触发：cron 周一 08:00 UTC（APScheduler 加 job，遍历有 active 绑定家长的学生）+ `POST .../weekly/generate` 手动 + `GET .../weekly` 懒生成。
- **备选**：新建 `weekly_report` 表。否决——当前无历史周报消费方，一表一迁移的复杂度不值得。

### D7 周报生成：独立 weekly_report_service.py，复用诊断 LLM 模式
与 `llm_diagnosis.py` 同构：按文档 33 §7 模板构建 prompt（术语转换表、输出约束、字数限制嵌入 system prompt）→ `FallbackDiagnosisLLMClient.complete` → `extract_json` → 解析 `{summary, detail, advice, no_data}` → 解析失败纠错重试 ≤3 → 降级错误信号。ai-summary 复用同一 prompt/解析基建，仅 user prompt 差异。不走 Agent（§7 是死模板，非对话）。

### D8 路由组织：新建 app/api/v1/parent.py
现有 `auth.py` 的 parent_router 仅含 `/login`，已挂载 `/api/parent`。新建 `parent.py` 定义 `parent_router`（bind/children/child-report/weekly/ai-summary/notifications），main.py 追加挂载。`/login` 留在 auth.py 不动（破坏面最小）。

### D9 Webhook 签名协议：HMAC-SHA256 + 时间戳防重放
课程 53 把 LMS 集成拉回 scope，但仅指"系统内可用"部分（设计文档 33 §10.4 注释）。新建 `app/services/integration/webhook_service.py`：触发时对规范化载荷（参数排序后拼接）取 HMAC-SHA256，secret 为各注册私有密钥，签名与时间戳随 `X-Webhook-Signature`/`X-Webhook-Timestamp` 请求头投递；接收方用 secret 验证来源、用时间戳窗口防重放。投递失败指数退避重试 ≤3，结果落库。**备选**：接入真实钉钉/企微 SDK——否决，需要外部账号不可离线测试，且设计文档 33 §10 明确不在版本范围。

### D10 Webhook 存储：独立 WebhookRegistration 模型
`app/db/models/integration.py`：`WebhookRegistration(id, event_type, url, secret_hash, enabled, created_at)`。secret 只存不可逆形式或掩码回显，明文不回传（spec「secret 不可读取回明文」）。事件类型由 `WebhookEventType` 枚举约束为 7 种。LMS 配置以 JSON 存入 `integration_config` 单例键值表（连接器启用标志 + 签名设置），沿袭 `MutableDict.as_mutable(JSON)` 模式。

### D11 事件触发点：只接两个自然埋点
`warning.triggered` 接入 `early_warning.py` 预警创建处、`practice.assigned` 接入 `daily.py` 布置处——两个模块本就在建家长通知，emit 顺路加，破坏面最小。其余 5 种事件类型**支持注册与手动触发**，但不在既有业务流埋点（避免大范围触碰 exam/student 稳定模块）。manual trigger 端点保证所有 7 类型可离线验收签名协议。

## Risks / Trade-offs

- [枚举值替换 BREAKING] → 单次 alembic 迁移内完成数据映射；依赖旧值的消费方在 tasks 中同步更新；开发期存量数据少。
- [周报懒生成在请求路径调用 LLM，耗时数秒] → 家长月用 2-4 次的低频场景可接受；失败返回可读错误而非半成品；cron 提前预热。
- [require_bound_child 遗漏导致越权] → 单一依赖函数 + 所有家长数据端点显式声明；access-control spec 枚举覆盖面；测试覆盖"未绑定 403 / 学生不存在 404 / 越权解绑"。
- [LLM 输出质量偏离通俗化约束] → prompt 强约束 + 长度限制 + 纠错重试；L3 evals 用文档 §7 样例校验。
- [Webhook 签名参数排序/拼接与接收方约定不一致] → 签名协议固定为规范化规则（排序+拼接+时间戳），design 记录规范，测试锁定签名向量。
- [Webhook 投递阻塞主流程] → 投递异步/带超时与重试，失败记日志不抛异常。
- [secret 泄露] → 只存不可逆形式、掩码回显、注册后不可回读明文。

## Migration Plan

1. 新 alembic migration：`NotificationType` 存量值映射更新（message→daily_report、warning→score_alert、report→weekly_report）、`ParentNotification` 加 `created_at`（非空默认 now，index）、`Student` 加 `weekly_report`(JSON)+`weekly_report_week`(date)、新建 `WebhookRegistration` 与 `integration_config` 表。
2. 迁移幂等；开发库直接 `alembic upgrade head`。回滚：`alembic downgrade` 还原枚举、列与表。

## Open Questions

- 周报 cron 是否仅限"有 active 绑定家长"的学生，还是全部学生先生成再按绑定过滤？——默认仅遍历有 active 绑定的学生（节省 LLM 调用），此细节不影响 spec/任务结构，实现时定。
