## Purpose

定义 ChemAI 的认证体系：基于纯 Python 自实现 HMAC-SHA256 JWT 的无状态认证，支持统一账户登录、家长独立登录、令牌验证与刷新，以及安全的密码存储。

## Requirements

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

### Requirement: JWT 令牌格式
系统 SHALL 使用 HMAC-SHA256 签名的 JWT，payload 包含用户 ID、角色、学校 ID、令牌类型、签发时间与过期时间；家长令牌不包含学校 ID。

#### Scenario: 教师令牌结构
- **WHEN** 教师登录签发 access token
- **THEN** 令牌可解码出用户 ID、角色（teacher）、学校 ID 与过期时间

#### Scenario: 家长令牌无学校 ID
- **WHEN** 家长登录签发 access token
- **THEN** 令牌中角色为 parent 且不包含学校 ID

### Requirement: 令牌验证
系统 SHALL 对受保护请求验证 Bearer token 的签名、过期时间与令牌类型；无效、过期或篡改的令牌均被拒绝。

#### Scenario: 有效令牌放行
- **WHEN** 请求携带签名正确且未过期的 access token
- **THEN** 请求通过认证并携带用户上下文

#### Scenario: 过期令牌拒绝
- **WHEN** 请求携带已过期的 access token
- **THEN** 返回 401 且错误码为 TOKEN_EXPIRED

#### Scenario: 篡改令牌拒绝
- **WHEN** 请求携带被修改过 payload 的令牌
- **THEN** 签名校验失败，返回 401 认证失败

### Requirement: 令牌刷新
系统 SHALL 提供刷新端点，用 refresh token 换取新的 access token。

#### Scenario: 刷新成功
- **WHEN** 提交有效且未过期的 refresh token
- **THEN** 返回新的 access token

#### Scenario: 刷新失败
- **WHEN** 提交过期或无效的 refresh token
- **THEN** 返回认证失败错误

### Requirement: 无状态会话
系统 SHALL 采用无状态 JWT 架构：服务端不保存会话与令牌黑名单，登出仅由客户端清除本地存储完成。

#### Scenario: 登出
- **WHEN** 客户端清除本地 access token 并重定向登录页
- **THEN** 服务端无需任何登出端点，token 到期前仍有效

### Requirement: 家长独立登录
系统 SHALL 提供独立的家长登录端点，接受手机号与绑定码，校验通过后签发 role 为 parent 的令牌。

#### Scenario: 家长登录成功
- **WHEN** 提交已绑定学生的手机号与正确绑定码
- **THEN** 返回家长令牌并关联该家长信息

#### Scenario: 绑定码错误
- **WHEN** 提交的绑定码与任何亲子绑定不匹配
- **THEN** 返回业务规则冲突错误，不签发令牌

### Requirement: 密码安全存储
系统 SHALL 以不可逆方式存储账户密码，不得以明文存储，并支持验证流程。

#### Scenario: 密码校验
- **WHEN** 用户提交密码用于登录
- **THEN** 系统以存储的不可逆散列验证密码，而不是比较明文
