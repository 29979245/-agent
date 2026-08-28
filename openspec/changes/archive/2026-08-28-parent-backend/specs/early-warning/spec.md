## MODIFIED Requirements

### Requirement: 预警通知链
系统 SHALL 在创建预警时向该学生所有状态为 `active` 的家长绑定推送 `ParentNotification`（通知类型 `score_alert`）；无活跃绑定则静默跳过。教师端通过 `GET /api/warning/pending` 主动查询预警列表，不推送。学生端通知标记 SHALL 预留但不发送。

#### Scenario: 存在活跃绑定家长
- **WHEN** 创建预警且该学生有 status=active 的家长绑定
- **THEN** 为每个活跃绑定家长创建一条 score_alert 类型通知，预警记录标记家长已通知

#### Scenario: 无活跃绑定家长
- **WHEN** 创建预警但该学生无活跃家长绑定
- **THEN** 不创建任何家长通知，预警记录标记家长未通知
