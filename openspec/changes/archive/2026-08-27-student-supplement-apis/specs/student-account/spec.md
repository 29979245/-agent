## Purpose

提供学生账户自服务能力：生成/重新生成 6 位家长绑定码，供学生「我的」页展示复制（家长端 doc 53 消费绑定码登录），学生仅可操作自身的绑定码。

## ADDED Requirements

### Requirement: 生成家长绑定码
系统 SHALL 提供 `POST /api/student/{student_id}/bind-code` 端点，为指定学生生成 6 位大写字母数字绑定码并写回 `Student.bind_code`；仅已认证角色可访问，学生角色仅可操作自身（非本人返回 403）。

#### Scenario: 本人生成绑定码成功
- **WHEN** 学生携带有效 token 请求 `POST /api/student/{student_id}/bind-code` 且 `student_id` 等于自身业务实体 ID
- **THEN** 返回 200，`Student.bind_code` 更新为新生成的 6 位大写字母数字码，响应含 `bind_code`

#### Scenario: 非本人生成被拒绝
- **WHEN** 学生携带有效 token 请求其他学生的绑定码生成
- **THEN** 返回 403 无权限，不修改任何数据

#### Scenario: 重新生成覆盖旧码
- **WHEN** 学生再次请求生成绑定码
- **THEN** 返回新的 6 位码并覆盖 `Student.bind_code` 旧值

### Requirement: 绑定码格式
系统 SHALL 生成的绑定码为 6 位大写字母与数字组合，不含易混淆字符（如 0/O、1/I）。

#### Scenario: 格式校验
- **WHEN** 绑定码生成成功
- **THEN** 返回的码长度 6、全为大写字母或数字、不含易混淆字符

### Requirement: 绑定码读取
系统 SHALL 使学生能读取自身的绑定码，报告聚合端点（`report` 能力）的 `profile.bind_code` 返回当前值。

#### Scenario: 报告页显示绑定码
- **WHEN** 学生读取自身个人报告
- **THEN** `profile.bind_code` 为当前 `Student.bind_code` 值（未生成时为空字符串）
