## MODIFIED Requirements

### Requirement: 统一账户登录
系统 SHALL 提供登录端点，接受用户名、密码与角色，校验通过后签发 access token 与 refresh token，并在响应中返回业务实体 ID（`role_id`，学生即 `Student.id`），供客户端定位自身资源。

#### Scenario: 登录成功
- **WHEN** 提交正确的用户名、密码与角色
- **THEN** 返回 access token（24 小时有效）、refresh token（7 天有效）、用户 ID、姓名、角色与业务实体 ID（`role_id`）

#### Scenario: 登录失败
- **WHEN** 提交错误的密码
- **THEN** 返回认证失败错误，不签发任何 token

#### Scenario: 待审核教师拒绝登录
- **WHEN** 提交处于"待审核"状态的教师账户凭据
- **THEN** 登录被拒绝并返回业务规则冲突错误，不签发任何 token
