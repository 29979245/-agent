## Purpose

为学生提供移动端登录入口与角色分流：student 角色登录后进入学生端（练习页），将登录响应携带的业务实体 ID（role_id）解析为 student_id 持久化，供练习/复习/错题接口使用；教师登录仍进入出题工作台。

## Requirements

### Requirement: 学生登录与角色分流

系统 SHALL 提供学生登录页面，接受学号/手机号与密码；student 角色凭据验证成功后 SHALL 路由至学生端入口（练习页），teacher 及教师角色 SHALL 仍路由至出题工作台，未登录访问学生页 SHALL 跳转登录页。

#### Scenario: 学生登录进入学生端

- **WHEN** 学生凭正确账号密码在学生登录页提交
- **THEN** 登录成功并跳转至学生端练习页，而非教师工作台

#### Scenario: 教师登录仍进工作台

- **WHEN** 教师凭正确账号密码登录
- **THEN** 登录成功并跳转至出题工作台

#### Scenario: 未登录访问跳转登录

- **WHEN** 未携带有效登录态访问学生练习/复习/错题页
- **THEN** 跳转至学生登录页

#### Scenario: 凭证错误提示

- **WHEN** 提交错误的账号或密码
- **THEN** 登录失败并提示"账号或密码错误"，不进入任何页面

### Requirement: 业务实体 ID 解析

系统 SHALL 从登录响应中的 `role_id` 解析当前学生的 `student_id` 并持久化到本地登录态；学生端所有列表类请求（练习任务/复习任务/错题列表）SHALL 以该 `student_id` 作路径参数发起。

#### Scenario: 登录后解析 student_id

- **WHEN** 学生登录成功且响应包含 `role_id`
- **THEN** 前端将 `role_id` 存为 `student_id`，后续练习/复习/错题请求均以其作路径参数

#### Scenario: 登录态缺失兜底

- **WHEN** 登录态中缺少 `student_id` 或令牌已失效
- **THEN** 前端回到登录页重新登录，不发起业务请求
