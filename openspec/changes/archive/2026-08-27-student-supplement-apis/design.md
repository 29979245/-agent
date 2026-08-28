## Context

「我的」页（doc 40 §2.5）需要个人资料、学习统计、学习周报、学习计划与绑定码，但后端现有路由（auth/exam/practice/review/wrong-question/panel/diagnosis/warning/exam-bank/audit/parent）均不覆盖这些数据。`Student` 模型已具备 `bind_code`（String(6) 默认空串）与 `learning_plan`（JSON 默认空）字段，`practice.py` 已有 `_role_id`（account.id → 业务实体 id）与 `_practice_records`（practice 类型记录、排除 training/variant）可复用的查询模式。密码散列 `hash_password`/`verify_password`（PBKDF2）与 `require_permission(resource, action)` 装饰器均已就绪。详见 proposal.md Why 与 specs 三份契约。

## Goals / Non-Goals

**Goals:**
- 三个补充端点覆盖「我的」页全部数据需求，一次请求闭环页面渲染。
- 学生仅可访问/操作自身数据（复用 `_role_id` + self-check 403 模式）。
- 权限矩阵以最小增量扩展（新增 `report`、`account` 两个资源）。

**Non-Goals:**
- 不实现 Agent SSE 对话后端（doc 55-59）、通知/家长端（doc 53）、Evals（doc 54）。
- 不实现周报教师评语 LLM 生成（doc 57），本轮 `teacher_comment` 固定 `null`。
- 不实现家长绑定流程本身（doc 53）——本轮只生成绑定码供学生展示。

## Decisions

### D1: 报告聚合单端点返回「我的」页全部数据
`GET /api/report/student/{student_id}` 一次返回 `profile` + `stats` + `weekly` + `learning_plan`，而非拆成 profile/stats/report/plan 多个端点。
- **备选**：拆 4 个端点各自返回。移动端单页渲染需 4 次往返、前端拼接，复杂且每次请求都重复做学生归属校验。
- **取舍**：单端点响应体较大（含周报知识点数组），但数据量对移动端可接受；`teacher_comment` 为 null 占位不引入 LLM 延迟。
- 聚合逻辑放 `app/services/analytics/report_service.py` 的 `build_student_report(db, student_id)`，端点只做鉴权 + self-check + 调服务，保持路由薄、聚合逻辑可单测。

### D2: 权限矩阵最小增量
`ROLE_PERMISSIONS` 的 `RESOURCES` 增加 `report` 与 `account`：
- `report`: admin 全 CRUD（`_full_crud(RESOURCES)` 自动覆盖）、subject_lead 只读（自动覆盖）、teacher `{read}`、student `{read, update}`。
- `account`: 各矩阵角色 `{update}`（改自身密码，所有已认证角色都可改自己的）。
- 三个端点映射：报告读取 → `require_permission("report","read")` + self-check；绑定码生成 → `require_permission("report","update")` + self-check；改密码 → `require_permission("account","update")` + 自身校验（路径无 student_id，直接用 `request.state.user.user_id`）。
- **备选**：复用现有 `practice` 资源（practice.py 已如此松动复用）——但 report 与 practice 语义无关，为读报告/改绑定码引入 practice 权限会污染矩阵语义。
- **取舍**：新增两个资源是干净的模型扩展，代价是权限矩阵测试需同步更新。

### D3: 绑定码生成
`POST /api/student/{student_id}/bind-code`：用 `secrets` 从排除易混淆字符（0/O/1/I）的字母数字集采样 6 位，写回 `Student.bind_code`，返回 `{bind_code}`。重新生成覆盖旧值。
- **风险**：重新生成会使旧的 `StudentParentBinding` 匹配失效（家长登录用 bind_code 匹配）。家长绑定是 doc 53 范围，本轮仅记录风险；重生成场景下旧码失效属预期行为（学生主动重新生成即主动换码）。

### D4: 修改密码
`POST /api/auth/change-password`（请求体 `{old_password, new_password}`）：
- 取 `request.state.user.user_id` → `Account`，`verify_password(old_password, account.password)` 失败 → 400（错误码 `BUSINESS_RULE_VIOLATION`）。
- 通过后 `hash_password(new_password)` 更新 `account.password`；`new_password` Pydantic `min_length=6`。
- 复用 `auth.py` 已导入的 `hash_password`/`verify_password`。

### D5: 统计口径与聚合实现
- 练习记录：复用 `_practice_records` 等价查询（`ExamRecord` type=practice、排除 training/variant、按时间倒序）。
- `stats.completed_exercises`：完成过作答的练习数（该练习存在 `StudentAnswer`）。
- `stats.accuracy`：全部已判作答的正确率（答对/总作答）。
- `stats.streak_days`：对 `StudentAnswer.answered_at` 取日期去重，从今天向前数连续天数。
- `weekly`：只统计本周一 00:00 至今的练习与作答；`knowledge_points` 用 `split_knowledge_points(q.knowledge_points)` 拆分题目知识标签，逐知识点聚合正确率；`week_label` 用 ISO 周（如 `2026-W35`）。
- 空数据：所有指标为 0、数组为空、`learning_plan=null`、`teacher_comment=null`（对应 spec 场景）。

### D6: 路由组织与挂载
- 新增 `app/api/v1/report.py`（`report_router`，前缀 `/api/report`）→ 报告读取。
- 新增 `app/api/v1/student.py`（`student_router`，前缀 `/api/student`）→ 绑定码生成。
- 修改 `app/api/v1/auth.py` → `POST /change-password`。
- `app/main.py` 挂载 `report_router`、`student_router`（`auth_router` 已挂载）。

## Risks / Trade-offs

- **[绑定码重生成使家长旧绑定失效]** → 属预期行为（主动换码）；家长绑定流程 doc 53 落地时按新码消费，本轮只记录。
- **[权限矩阵变更影响既有测试]** → `tests/unit/test_permissions` 等断言需同步更新；`_full_crud(RESOURCES)` 使 admin/subject_lead 自动获得新资源权限，逐一核对测试快照。
- **[报告聚合性能]** → 聚合涉及多次查询（练习、作答、知识点），学生作答量级小（中学练习场景），无需缓存；若未来数据量大可加缓存，本轮不做。
- **[教师读取学生报告权限]** → 矩阵给 teacher `report: {read}` 但无 self-check 约束（教师读任意学生报告），与权限矩阵「教师可读学生数据」一致（对照 `student: {read}` 教师已有）。

## Migration Plan

1. 权限矩阵：`permissions.py` 加资源 + 矩阵行，同步更新权限单测 → 独立 commit（scope `auth`）。
2. 后端三个端点按 TDD 逐个实现：report 聚合服务 → report 端点 → student 绑定码端点 → auth 改密 → 各配单元测试（scope `report` / `auth`）。
3. `main.py` 挂载新路由 + 冒烟测试确认可达。
4. 回滚：移除 `report_router`/`student_router` 挂载与矩阵新资源即回滚，无数据迁移。

## Open Questions

无（端点契约、统计口径、权限归属均已定，细节可在任务层细化）。
