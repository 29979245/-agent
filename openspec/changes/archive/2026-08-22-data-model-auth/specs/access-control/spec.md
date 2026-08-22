## Purpose

定义 ChemAI 的权限控制体系：以 ROLE_PERMISSIONS 矩阵为权威数据源的三层权限检查（全局认证中间件、资源级检查器、端点级声明装饰器），以及家长"最小可见"的数据访问边界。

## ADDED Requirements

### Requirement: ROLE_PERMISSIONS 权限矩阵
系统 SHALL 以角色 × 资源 × 操作的三维矩阵作为全部权限校验的权威数据源，覆盖 admin、教务管理员、学科组长、teacher、student 五类角色与 school/grade/class/teacher/student/analysis/exam/question/ocr/grading 十类资源。

#### Scenario: 矩阵授权
- **WHEN** 检查 teacher 角色对 exam 资源的 create 操作
- **THEN** 权限矩阵返回允许

#### Scenario: 矩阵拒绝
- **WHEN** 检查 student 角色对 exam 资源的 create 操作
- **THEN** 权限矩阵返回拒绝

#### Scenario: 只读角色
- **WHEN** 检查学科组长角色对任何资源的写操作
- **THEN** 权限矩阵返回拒绝，仅 read 操作允许

#### Scenario: 家长默认拒绝
- **WHEN** 检查 parent 角色对矩阵内任一资源的任一操作
- **THEN** 权限矩阵返回拒绝（parent 不入矩阵，走独立认证路径，对矩阵资源默认拒绝）

### Requirement: 全局认证中间件
系统 SHALL 对 `/api/*` 请求执行 JWT 令牌验证，白名单路径跳过认证直接放行，缺失或无效令牌返回 401。

#### Scenario: 白名单路径放行
- **WHEN** 请求路径以 `/api/auth/` 开头且未携带 token
- **THEN** 请求跳过认证进入后续处理

#### Scenario: 缺失令牌拒绝
- **WHEN** 请求非白名单路径且未携带 Authorization 头
- **THEN** 返回 401 认证失败

#### Scenario: 无效令牌拒绝
- **WHEN** 请求携带无法通过验证的 Bearer token
- **THEN** 返回 401 认证失败

### Requirement: 资源级权限检查
系统 SHALL 提供资源级权限检查器，接收资源与操作两个参数，依据 JWT 中的角色在权限矩阵中判定，并返回包含用户 ID、角色、学校 ID 的用户上下文。

#### Scenario: 有权限通过
- **WHEN** 教师请求访问 exam 资源的 create 权限
- **THEN** 检查通过并返回教师用户上下文

#### Scenario: 无权限拒绝
- **WHEN** 学生请求访问 question 资源的 create 权限
- **THEN** 检查拒绝并返回 403 权限不足

### Requirement: 端点级权限声明
系统 SHALL 提供 `require_permission` 装饰器，在端点级别声明所需资源与操作，越权访问返回 403。

#### Scenario: 端点声明生效
- **WHEN** 请求触发带 `require_permission("exam", "create")` 的端点且角色无权限
- **THEN** 返回 403 且错误码为 PERMISSION_DENIED

### Requirement: 家长最小可见数据边界
> 注：本需求依赖行级数据范围过滤与业务查询端点，显式推迟到阶段三；本阶段仅建立 parent 独立认证路径与矩阵默认拒绝（见 ROLE_PERMISSIONS 权限矩阵）。

系统 SHALL 确保家长仅能访问本人绑定子女的数据；对绑定关系不生效的请求返回 403。

#### Scenario: 访问绑定子女数据
- **WHEN** 家长请求其生效绑定子女的考试数据
- **THEN** 请求被允许并仅返回该子女的数据

#### Scenario: 访问未绑定学生数据
- **WHEN** 家长请求某非绑定学生的数据或班级统计
- **THEN** 返回 403 权限不足，且响应不泄露任何学生个人信息

### Requirement: 认证与授权错误区分
系统 SHALL 区分认证失败与授权失败：未认证返回 401 与 AUTHENTICATION_REQUIRED/TOKEN_EXPIRED 错误码，已认证但无权限返回 403 与 PERMISSION_DENIED 错误码。

#### Scenario: 未认证
- **WHEN** 请求未携带有效令牌
- **THEN** 返回 401 与对应的认证错误码

#### Scenario: 已认证但越权
- **WHEN** 请求携带有效令牌但角色对资源无权限
- **THEN** 返回 403 与 PERMISSION_DENIED 错误码
