## 1. 权限矩阵扩展

- [x] 1.1 `app/core/permissions.py` 的 `RESOURCES` 增加 `report`、`account`；矩阵给学生开 `report: {read, update}`、各矩阵角色开 `account: {update}`、教师开 `report: {read}`，并核对 admin/subject_lead 因 `_full_crud(RESOURCES)`/全只读自动获得的新权限。验证：更新 `tests/unit/test_permissions.py`（或等价权限测试）断言 `has_permission("student","report","read")` 等为真、未授权操作为假，`pytest tests/unit/test_permissions.py` 通过
- [x] 1.2 对照现有权限测试快照逐一核对新增资源未破坏旧断言（admin 全 CRUD 覆盖 `report`/`account`、subject_lead 全只读、dept_admin 不受影响）。验证：`pytest tests/unit/ -k permission -x` 通过

## 2. 报告聚合服务

- [x] 2.1 新增 `app/services/analytics/report_service.py` 的 `build_student_report(db, student_id)`：聚合 profile（name/class_name/bind_code）、stats（completed_exercises/accuracy/streak_days）、weekly（week_label/exercises/accuracy/duration_hours/knowledge_points/teacher_comment=null）、learning_plan（空为 null）。验证：新增 `tests/unit/test_report_service.py` 覆盖空数据、有数据、training/variant 排除、streak_days 连续口径、知识点聚合
- [x] 2.2 统计口径实现：练习记录复用 `ExamRecord` type=practice 排除 training/variant；`accuracy` 用已判作答答对/总作答；`streak_days` 对 `StudentAnswer.answered_at` 去重日期从今向前数连续；`weekly` 限本周一至今；`knowledge_points` 用 `split_knowledge_points` 拆分题目标签聚合正确率。验证：`pytest tests/unit/test_report_service.py` 通过，覆盖连续打卡断档与跨周边界

## 3. 报告读取端点

- [x] 3.1 新增 `app/api/v1/report.py`（`report_router`，前缀 `/api/report`）：`GET /student/{student_id}`，`require_permission("report","read")` + `_role_id` self-check（学生非本人 403），调 `build_student_report` 返回。验证：新增 `tests/unit/test_report_api.py` 覆盖本人 200、非本人 403、教师读学生 200、未认证 401
- [x] 3.2 `app/main.py` 挂载 `report_router`。验证：`tests/integration/test_smoke.py`（或等价冒烟）确认 `/api/report/student/{id}` 路由可达且未带 token 返回 401

## 4. 绑定码生成端点

- [x] 4.1 新增 `app/api/v1/student.py`（`student_router`，前缀 `/api/student`）：`POST /{student_id}/bind-code`，`require_permission("report","update")` + self-check（学生非本人 403），`secrets` 采样排除 0/O/1/I 的 6 位大写字母数字写回 `Student.bind_code`，返回 `{bind_code}`。验证：新增 `tests/unit/test_student_api.py` 覆盖生成格式（长度 6、无易混淆字符）、本人 200、非本人 403、重新生成覆盖旧值
- [x] 4.2 `app/main.py` 挂载 `student_router`。验证：冒烟测试确认 `/api/student/{id}/bind-code` 路由可达且未带 token 401

## 5. 修改密码端点

- [x] 5.1 `app/api/v1/auth.py` 新增 `POST /change-password`（`{old_password, new_password}`，`new_password` min_length=6）：`require_permission("account","update")`，取 `request.state.user.user_id` 对应 `Account`，`verify_password` 校验旧密码（失败 400 `BUSINESS_RULE_VIOLATION`），`hash_password` 更新。验证：新增 `tests/unit/test_auth_api.py` 用例覆盖旧密码错误 400、成功改密后旧密不可登录新密可登录、未认证 401
- [x] 5.2 更新 `tests/unit/test_auth_api.py` 既有断言不受新增端点影响。验证：`pytest tests/unit/test_auth_api.py` 通过

## 6. 全量验证

- [x] 6.1 运行 `pytest tests/ -x` 全量通过，确认权限矩阵变更未破坏既有端点（尤其 practice/review/wrong-question 的 `practice` 资源权限）。验证：全量测试绿
- [x] 6.2 手工走查：用 `student_demo` 账号登录 → 请求报告聚合端点返回 profile/stats/weekly/learning_plan → 生成绑定码后报告页 bind_code 更新 → 改密码后旧密登录失败新密成功。验证：三个端点响应符合 spec 契约
