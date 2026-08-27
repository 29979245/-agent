## ADDED Requirements

### Requirement: 修改密码
系统 SHALL 提供 `POST /api/auth/change-password` 端点，允许已认证用户校验旧密码后更新自身密码；新密码以不可逆散列存储。

#### Scenario: 修改密码成功
- **WHEN** 已认证用户提交正确的旧密码与新的密码
- **THEN** 返回成功，账户密码更新为新密码的不可逆散列

#### Scenario: 旧密码错误
- **WHEN** 已认证用户提交错误的旧密码
- **THEN** 返回业务规则冲突错误，密码不更新

#### Scenario: 未认证请求被拒绝
- **WHEN** 未携带有效 token 请求修改密码
- **THEN** 返回 401 未认证，密码不更新
