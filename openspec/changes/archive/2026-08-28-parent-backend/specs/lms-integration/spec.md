## Purpose

Webhook 事件系统与 LMS 配置 API：让外部系统通过注册 Webhook 接收 ChemAI 的业务事件（练习布置、成绩预警等），事件载荷经 HMAC-SHA256 签名保证可验证来源；集成配置与同步端点为未来 LMS 连接器预留（外部连接器不在当前版本范围）。

## ADDED Requirements

### Requirement: Webhook 注册管理
系统 SHALL 提供 Webhook 注册、列表与取消端点，支持的事件类型 SHALL 限于 7 种：`practice.assigned`（练习布置）、`practice.completed`（练习完成）、`exam.created`（考试创建）、`exam.graded`（考试成绩发布）、`warning.triggered`（预警触发）、`student.login`（学生登录）、`review.due`（复习任务到期）。注册时 SHALL 记录事件类型、回调 URL 与签名密钥（secret），secret 不可被读取回明文。

#### Scenario: 注册 Webhook
- **WHEN** 提交一种受支持的事件类型、回调 URL 与 secret
- **THEN** 创建一条启用状态的 Webhook 注册，返回注册标识，secret 不回显明文

#### Scenario: 注册不支持的事件类型
- **WHEN** 提交 7 种类型之外的事件类型
- **THEN** 返回数据校验错误，不创建注册

#### Scenario: 列出已注册 Webhook
- **WHEN** 请求 Webhook 列表
- **THEN** 返回全部注册（事件类型、URL、启用状态、创建时间），secret 以掩码显示

#### Scenario: 取消注册
- **WHEN** 按事件类型取消一个已注册 Webhook
- **THEN** 该注册被移除，不再接收该类型事件推送

### Requirement: Webhook 签名投递
系统 SHALL 在触发事件时向该事件类型的全部启用注册投递载荷：载荷 SHALL 经 HMAC-SHA256 签名（签名串包含时间戳与规范化参数），签名与时间戳随请求头携带，供接收方验证来源与防重放；投递失败 SHALL 重试并记录结果。

#### Scenario: 签名投递成功
- **WHEN** 触发一个已注册事件类型
- **THEN** 向该类型全部启用注册 POST 带签名与时间戳头的载荷，接收方可用 secret 验证签名

#### Scenario: 投递失败重试
- **WHEN** 首次投递返回失败
- **THEN** 按策略重试，仍失败则记录失败结果且不阻断主流程

#### Scenario: 无启用注册
- **WHEN** 触发的事件类型无启用注册
- **THEN** 不产生任何投递，事件处理正常返回

### Requirement: Webhook 手动触发
系统 SHALL 提供 `POST /api/integration/webhooks/trigger/{event_type}`，手动触发指定事件类型，向该类型全部启用注册投递签名载荷。

#### Scenario: 手动触发事件
- **WHEN** 手动触发一个受支持的事件类型
- **THEN** 向该类型全部启用注册执行签名投递并返回投递结果汇总

### Requirement: 事件触发点接入
系统 SHALL 在既有业务发送点接入真实事件触发：创建预警时触发 `warning.triggered`（载荷含预警类型/标题/学生 ID）；每日练习布置时触发 `practice.assigned`（载荷含练习名/学生 ID）。其余事件类型支持注册与手动触发，实际业务埋点不在本版本范围。

#### Scenario: 预警触发事件
- **WHEN** 系统创建一条预警
- **THEN** 触发 `warning.triggered` 事件投递

#### Scenario: 每日练习触发事件
- **WHEN** 系统布置每日练习
- **THEN** 触发 `practice.assigned` 事件投递

### Requirement: LMS 配置与同步
系统 SHALL 提供 `GET/POST /api/integration/lms/config` 存取集成配置（连接器启用标志与签名设置），配置为 JSON 结构持久化；`POST /api/integration/lms/sync` 手动触发数据同步，在外部连接器未启用时返回状态说明而非报错。

#### Scenario: 读取配置
- **WHEN** 请求 LMS 配置
- **THEN** 返回当前集成配置 JSON

#### Scenario: 保存配置
- **WHEN** 提交合法的集成配置 JSON
- **THEN** 配置被持久化，后续读取返回更新后的值

#### Scenario: 同步状态占位
- **WHEN** 外部连接器均未启用时触发同步
- **THEN** 返回状态说明（无启用连接器），不产生实际同步行为也不报错
