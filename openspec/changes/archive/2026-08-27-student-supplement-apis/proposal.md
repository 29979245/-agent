## Scope

- **Scope**: `ui (student)` —— 对齐 doc 52 文档头，属学生端页面补充范围
- **Git 分支**: `phase-4/business`
- **性质**: 仅学生端页面所需的**补充性质 API 端点**（doc 52 Phase A），不含 Agent/通知/家长端等后续文档实现

## Why

学生端练习/错题/复习三页的后端 API 已就绪，但「我的」页（doc 40 §2.5 个人报告与设置）所需的数据端点尚缺：个人报告聚合、绑定码生成、修改密码均无后端实现。doc 52 学生端 6 页面中 report 页无法闭环。

## What Changes

- 新增学生个人报告聚合端点 `GET /api/report/student/{student_id}`：一次返回「我的」页全部数据——个人信息卡（姓名/班级/绑定码）、学习统计卡（完成练习/正确率/连续打卡）、学习周报（本周练习/正确率/时长 + 知识点掌握度 + `teacher_comment`，本轮为 `null`）、学习计划（`Student.learning_plan`）。学生仅可查自身（self-check 403）。
- 新增绑定码生成端点 `POST /api/student/{student_id}/bind-code`：生成/重新生成 6 位大写字母数字绑定码，写回 `Student.bind_code`，供「我的」页展示复制与家长（doc 53）绑定登录。学生仅可改自身。
- 新增修改密码端点 `POST /api/auth/change-password`：校验旧密码后更新为不可逆散列新密码。任何已认证角色可改自身密码。
- 权限矩阵扩展：新增 `report` 资源（student 读/更新）与 `account` 资源（各角色更新自身），并接入三个新端点。

### 非目标（后续文档实现，本轮不做）

- Agent SSE 对话端点与 Student persona —— doc 55-59；doc 52 index.html 前端建真页，联调用前端 mock SSE，不进业务路由。
- 通知 / 家长端 / LMS —— doc 53。
- Evals 评测体系 —— doc 54。
- 学习周报「教师评语」的 LLM 生成 —— doc 57 `weekly_report` 工具，本轮固定返回 `teacher_comment: null`。

## Capabilities

### New Capabilities

- `report`: 学生个人报告聚合端点 `GET /api/report/student/{student_id}` —— 返回个人资料、学习统计、学习周报、学习计划，学生仅可读自身。
- `student-account`: 学生账户自服务端点 `POST /api/student/{student_id}/bind-code` —— 生成/重新生成家长绑定码，学生仅可改自身。

### Modified Capabilities

- `auth`: 增加「修改密码」要求 —— `POST /api/auth/change-password`，校验旧密码、以不可逆散列更新新密码，任何已认证角色可改自身。

## Impact

- `app/api/v1/report.py`（新增）：`GET /report/student/{student_id}` 聚合端点。
- `app/api/v1/student.py`（新增）：`POST /student/{student_id}/bind-code` 端点。
- `app/api/v1/auth.py`（修改）：新增 `POST /change-password` 端点。
- `app/core/permissions.py`（修改）：`RESOURCES` 增加 `report`、`account`；矩阵为学生开 `report: {read, update}`、各矩阵角色开 `account: {update}`。
- `app/main.py`（修改）：挂载 `report_router`（`/api/report`）与 `student_router`（`/api/student`）。
- 数据源：`Student`（name/class_id/bind_code/learning_plan）、`Class`、`ExamRecord`（practice 记录）、`StudentAnswer`（作答/正确率/连续打卡/知识点掌握）。
- 前端参考：`stitch-prototypes/m/report.html`、doc 40 §2.5。
- 测试：单元测试覆盖三个端点（鉴权、self-check 403、数据聚合、绑定码格式、改密旧密码校验）。
