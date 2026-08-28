## Context

后端练习/复习/错题 API（`practice-api`、`review`、`wrong-question`、`adaptive-practice-engine`、`daily-practice` 主规格）已全部就绪并归档。前端现状：`chemai-backend/frontend/pages/` 采用零构建多页模式——独立 HTML + 本地 vendor（Vue3 prod、KaTeX、mhchem、marked、fonts）+ `js/api.js`（`window.ChemAPI` fetch 封装，Bearer token 取自 `localStorage('chemai_token')`，401 跳登录、403 抛 ApiError）+ `js/auth.js`（`window.ChemAuth`，`TEACHER_ROLES` 门控）。现有 `login.html` 面向教师。视觉基准：设计文档 36（色板/字体/组件）+ 原型 `stitch-prototypes/m/{practice,review,wrong}.html`（430px 移动、topbar 56px、底部 4-tab tabbar）。

## Goals / Non-Goals

**Goals:**
- 4 个新页面：`student-login.html`、`practice.html`、`review.html`、`wrong.html`，复用现有零构建 + 本地 vendor 模式与设计 tokens。
- ChemAPI 增加 9 个学生方法；登录响应携带 `role_id` 并解析为 `student_id` 持久化。
- 仅复用现有后端端点，不新增任何业务 API（后端改动 = auth 登录响应加 `role_id`；另为满足复习/错题统计需求，`GET /api/review/tasks/{student_id}` 与 `GET /api/wrong-questions/{student_id}` 响应各增量加 `stats` 字段）。

**Non-Goals:**
- AI 助教对话与个人报告/设置页——tab 占位。
- 家长端页面、变式题 LLM 生成（后端确定性抽样已够）。
- 引入前端构建工具 / 新依赖 / SPA 路由。

## Decisions

### D1: 后端改动 = 登录响应加 `role_id` + review 端点返回统计
`_issue_tokens`（`app/api/v1/auth.py:102`）返回体加一个键 `"role_id": account.role_id`。所有学生列表端点已用 `_role_id(db,user)`（=`account.role_id`）作路径参数并 403 校验本人，客户端只需知道该值即可发起请求。加字段是纯增量、非 BREAKING，教师/家长同受益。
另：`GET /api/review/tasks/{student_id}` 响应增量加 `stats`（`due`=待复习数、`done_today`=今日已复习数（当日 ReviewHistory 条数）、`mastered`=已掌握数（done 任务数）），`GET /api/wrong-questions/{student_id}` 响应增量加 `stats`（`total`=错题总数、`week_new`=近 7 天错题数、`mastered`=已掌握数），满足复习页/错题本统计需求；`count`/`tasks`/`items` 等既有键不变。
- 备选：新增 `GET /api/auth/me` 返回业务 id——多一个端点与鉴权面，无必要。

### D2: student_id 生命周期
`ChemAuth.saveSession(token,user)` 扩展为同时持久化 `role_id`；新增 `ChemAuth.getStudentId()` 读取 `localStorage('chemai_user').role_id`。页面加载时若 token 缺失或 student_id 缺失 → 跳 `student-login.html`。所有列表请求以该值作 `{student_id}` 路径参数。

### D3: 角色分流
`auth.js` 的 `ChemAuth` 增加 `isStudent()`；学生登录成功后路由到 `practice.html`，教师角色仍路由到工作台（`exam-v2.html`）。新增 `student-login.html` 复用同一 `/api/auth/login`（服务端按账号角色返回 `role`，无需前端传角色），登录失败抖动 + 错误提示（doc 40 §2.6）。

### D4: 练习答题复用读卷接口
任务列表返回元数据不含题目，答题界面用 `GET /api/exam/{exam_id}/questions` 取题——该端点已按角色 `include_answer = role != "student"` 剥离答案与解析（`exam.py:136`），且 student 在权限矩阵有 `exam:read`。`exam_id` = `practice_id`（练习记录是 `ExamRecord`，id 即练习 id）。提交走 `POST /api/practice/submit {practice_id, answers:[{question_id, selected_option}]}`。

### D5: 页面导航结构
非 SPA 多页。底部 4-tab tabbar（AI助教/练习/错题/我的）在四页共享，激活态区分。入口映射：
- 练习 tab → `practice.html`；错题 tab → `wrong.html`；登录后默认落练习页。
- `review.html` 为二级页（原型 back→report、tabbar 激活错题），入口 = 我的占位页的功能入口列表（对齐 doc 40 §2.5）+ 错题页顶部"复习中心"入口。
- AI助教、我的 = 轻量占位页（同 tabbar 布局 + "建设中"提示），我的占位页列出「错题本/复习中心」静态入口承担导航中枢。

### D6: ChemAPI 学生方法
`api.js` 新增（沿用既有 `request()` 封装与错误处理）：
- `studentPracticeTasks(student_id)` → `GET /practice/student/{uid}/tasks`
- `studentPracticeSubmit(practice_id, answers)` → `POST /practice/submit`
- `studentEffect(student_id)` → `GET /practice/effect/{student_id}`
- `getExamQuestions(exam_id)` → `GET /exam/{exam_id}/questions`（若教师侧已存在则复用）
- `studentReviewTasks(student_id)` → `GET /review/tasks/{student_id}`
- `studentReviewSubmit(review_task_id, passed)` → `POST /review/submit`
- `studentWrongQuestions(student_id)` → `GET /wrong-questions/{student_id}`
- `studentWrongVariants(question_id, count)` → `POST /wrong-questions/variants`
- `studentWrongTrain(student_id, answers)` → `POST /wrong-questions/train`
- `studentWrongMastered(question_id, student_id)` → `POST /wrong-questions/{qid}/mastered`

### D7: 演示种子数据
新增幂等脚本 `scripts/seed_student_demo.py`：确保 `student_demo` 账号存在；调用 `daily`/`adaptive` 服务为它生成一份 `mode=daily` 练习记录并落若干 `StudentAnswer`（含几条答错，触发 `ReviewTask`），使三页均有真实数据。种子直接调用 service 层落库（不走 HTTP），复用 `WrongQuestionTrainer`/`SpacedRepetitionEngine` 建任务。

## Risks / Trade-offs

- **登录响应变更影响现有前端/测试** → `role_id` 为增量字段，更新 `tests/unit/test_auth_api.py` 断言其存在即可；`api.js`/`login.html` 不受影响（忽略未知键）。
- **学生态读卷与提交的题目 id 契约** → 冒烟验证 `GET /exam/{id}/questions` 返回的 `question_id` 与 submit 载荷一致（应为同一 Question 主键）；若序列化键名不符，在 `getExamQuestions` 适配层归一化。
- **演示数据缺失 → 页面空态** → 种子脚本必须为 `student_demo` 造齐练习/错题/复习数据；空态组件仍需实现（spec 场景）。
- **403 跨学生/越权在客户端体验** → `ChemAPI` 的 `handleAuth` 已把 403 抛为 `ApiError`，页面 catch 显示"无权限"而非空白。
- **移动端 430px 视口** → 按原型 `max-width` 移动容器 + 底部 tabbar 固定；桌面打开时居中留白（与原型一致）。

## Migration Plan

1. 后端：`auth.py` 加 `role_id` + `test_auth_api.py` 断言 → 独立 commit（scope `auth`）。
2. 前端：`auth.js` 角色分流 → `api.js` 学生方法 → 4 页（student-login → practice → review → wrong）逐页 commit（scope `ui`）。
3. 种子：`scripts/seed_student_demo.py`（scope `chore`）→ 运行后 `student_demo` 三页有数据。
4. 回滚：后端 revert `auth.py` 单键即回滚，前端页面降级为仅教师可用；无数据迁移。

## Open Questions

无（导航入口、取题复用、student_id 来源均已决策；细节可延后到任务层）。
