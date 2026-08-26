## Purpose

预警引擎将学情数据从被动聚合视图升级为主动告警：定时扫描全部学生，命中连续未登录、成绩下滑、错题率过高三类规则即创建预警记录并推送家长通知，教师在 `/api/warning` 完成处理闭环。

## Requirements

### Requirement: 连续未登录预警检测

系统 SHALL 检测学生连续未登录时长，超过阈值即创建 `no_login` 类型预警（级别固定为 `warning`）。登录时间由该学生最近一次作答时间（`max(answered_at)`）推导；若学生从未作答，则用注册时间（`created_at`）计算。连续未登录阈值 SHALL 为 3 天（周末 + 1 个工作日缓冲）。`created_at` 未知（既有数据未回填）的学生跳过该检测。

#### Scenario: 曾作答但超 3 天未使用

- **WHEN** 学生最近一次作答距今 >= 3 天
- **THEN** 创建一条 `no_login` 预警，级别 `warning`，结构化数据含未登录天数

#### Scenario: 从未作答且注册超 3 天

- **WHEN** 学生从未有任何作答且 `(now - created_at).days >= 3`
- **THEN** 创建一条 `no_login` 预警，级别 `warning`

#### Scenario: 未满 3 天不触发

- **WHEN** 学生最近作答距今 < 3 天（或从未作答但注册 < 3 天）
- **THEN** 不创建 `no_login` 预警

### Requirement: 成绩下滑预警检测

系统 SHALL 检测学生正式考试成绩下滑，仅聚合 `exam_type=exam` 且已发布/完成的考试（排除 per-student 练习，与学情面板均分口径一致）。取该学生最近两次考试成绩的正确率，按下式计算降幅：`drop_rate = (前次均分 - 最近均分) / 前次均分`。`drop_rate >= 0.1` 创建 `score_drop` 预警，`>= 0.2` 级别 `critical`，`0.1 <= drop_rate < 0.2` 级别 `warning`。前次均分为 0 或不足两次考试时不触发。

#### Scenario: 成绩下滑达临界

- **WHEN** 学生最近两次考试成绩降幅 `drop_rate >= 0.2`
- **THEN** 创建 `score_drop` 预警，级别 `critical`，结构化数据含降幅

#### Scenario: 成绩下滑达警告

- **WHEN** 学生最近两次考试成绩降幅 `0.1 <= drop_rate < 0.2`
- **THEN** 创建 `score_drop` 预警，级别 `warning`

#### Scenario: 无异常或数据不足

- **WHEN** 降幅 < 0.1，或该学生不足两场已发布/完成的正式考试，或前次均分为 0
- **THEN** 不创建 `score_drop` 预警

### Requirement: 错题率过高预警检测

系统 SHALL 检测学生最近一次考试/练习的整体错题率，`错误数 / 总题数 >= 0.7` 创建 `high_error_rate` 预警（级别 `warning`），`0.5 <= 错误率 < 0.7` 创建 `info` 级别预警。学生无任何作答时不触发。

#### Scenario: 错题率过高

- **WHEN** 学生最近一次考试/练习的错题率 >= 0.7
- **THEN** 创建 `high_error_rate` 预警，级别 `warning`

#### Scenario: 错题率需关注

- **WHEN** 学生最近一次考试/练习的错题率 >= 0.5 且 < 0.7
- **THEN** 创建 `high_error_rate` 预警，级别 `info`

#### Scenario: 无作答记录

- **WHEN** 学生没有任何作答记录
- **THEN** 不创建 `high_error_rate` 预警

### Requirement: 预警去重

系统 SHALL 在创建任何预警前检查是否已存在同一学生、同一类型、状态仍为 `pending` 的预警记录；若已存在则跳过，不重复创建。

#### Scenario: 已存在待处理同类型预警

- **WHEN** 学生已有一条 `pending` 状态的 `score_drop` 预警且再次命中成绩下滑规则
- **THEN** 不创建新的 `score_drop` 预警

#### Scenario: 不同类型互不影响

- **WHEN** 学生已有一条 `pending` 的 `no_login` 预警且成绩下滑规则命中
- **THEN** 创建一条新的 `score_drop` 预警

### Requirement: 预警通知链

系统 SHALL 在创建预警时向该学生所有状态为 `active` 的家长绑定推送 `ParentNotification`（通知类型 `warning`）；无活跃绑定则静默跳过。教师端通过 `GET /api/warning/pending` 主动查询预警列表，不推送。学生端通知标记 SHALL 预留但不发送。

#### Scenario: 存在活跃绑定家长

- **WHEN** 创建预警且该学生有 status=active 的家长绑定
- **THEN** 为每个活跃绑定家长创建一条 warning 类型通知，预警记录标记家长已通知

#### Scenario: 无活跃绑定家长

- **WHEN** 创建预警但该学生无活跃家长绑定
- **THEN** 不创建任何家长通知，预警记录标记家长未通知

### Requirement: WarningLog 数据模型

系统 SHALL 用 `WarningLog` 实体持久化每次预警，字段 SHALL 包含：唯一 ID、触发学生、预警类型（no_login/score_drop/high_error_rate）、严重级别（info/warning/critical）、标题、内容、结构化指标（JSON，含未登录天数/降幅/错题率等量化指标）、处理状态（pending/processed/ignored）、处理信息（处理人、处理时间、处理备注）、通知标记（教师/家长/学生是否已通知）、触发时间与处理时间。

#### Scenario: 预警创建落库

- **WHEN** 检测规则命中且通过去重
- **THEN** 写入一条 `WarningLog`，状态 `pending`，含标题/内容/结构化指标/级别/触发时间，通知标记记录通知结果

### Requirement: 待处理预警列表

系统 SHALL 提供 `GET /api/warning/pending` 端点，返回待处理（`pending`）预警列表，支持可选 `class_id` 筛选，按触发时间倒序。

#### Scenario: 查询待处理预警

- **WHEN** 教师请求待处理预警列表（可带 class_id）
- **THEN** 返回全部 pending 预警，含学生姓名/班级/类型/级别/标题/触发时间，按触发时间倒序

### Requirement: 学生预警历史

系统 SHALL 提供 `GET /api/warning/student/{student_id}` 端点，返回指定学生的全部预警历史（含已处理与已忽略），按触发时间倒序。

#### Scenario: 查询学生预警历史

- **WHEN** 教师请求某学生的预警历史
- **THEN** 返回该学生全部预警记录及其处理状态

### Requirement: 预警处理

系统 SHALL 提供 `PUT /api/warning/{warning_id}/process` 端点，请求体含 `action`（`processed` 或 `ignored`）与可选 `note`（教师备注）。`processed` 将状态置为 `processed`，`ignored` 置为 `ignored`，均记录处理人、处理时间与备注。

#### Scenario: 处理预警

- **WHEN** 教师对某条 pending 预警执行 action=processed（含 note）
- **THEN** 状态变为 `processed`，记录处理人/时间/备注，再次去重不再拦截

#### Scenario: 忽略预警

- **WHEN** 教师对某条 pending 预警执行 action=ignored
- **THEN** 状态变为 `ignored`

### Requirement: 手动触发预警检查

系统 SHALL 提供 `POST /api/warning/check` 端点，手动触发一次全量预警检查（与定时任务同逻辑），返回本次创建的预警数量与明细。

#### Scenario: 手动触发

- **WHEN** 教师调用 `/api/warning/check`
- **THEN** 执行全量检测并返回创建预警数与失败数

### Requirement: 班级预警汇总

系统 SHALL 提供 `GET /api/warning/class/{class_id}/summary` 端点，返回班级预警汇总：总预警数、按类型分布、按级别分布、`critical` 级别紧急预警数。

#### Scenario: 查询班级汇总

- **WHEN** 教师请求某班级预警汇总
- **THEN** 返回 total、by_type、by_level、critical_count

### Requirement: 定时预警检查

系统 SHALL 在每日 UTC 00:00 由 APScheduler 触发一次全量预警检查（`check_all_warnings()`），与手动触发共用同一实现。

#### Scenario: 定时任务触发

- **WHEN** 调度器每日 00:00 UTC 执行
- **THEN** 遍历全部学生执行三类检测，创建预警与推送通知，并输出创建数量日志

### Requirement: 预警权限控制

系统 SHALL 限制 `/api/warning` 端点仅教师角色可访问（admin / dept_admin / subject_lead / teacher），学生与家长角色不可访问。班级范围数据隔离沿用组织链：教师仅可查看本校学生预警，admin 及以上角色不受班级范围限制。

#### Scenario: 教师访问本校预警

- **WHEN** 教师访问本校学生的预警数据
- **THEN** 返回数据

#### Scenario: 教师访问他校预警

- **WHEN** 教师访问非本校学生的预警数据
- **THEN** 返回 403

#### Scenario: 学生访问预警

- **WHEN** 学生角色访问任意预警端点
- **THEN** 返回 403
