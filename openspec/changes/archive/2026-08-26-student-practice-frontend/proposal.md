## Why

后端自适应练习 / 间隔复习 / 错题强化训练 API（adaptive-practice-review-backend 已归档合入）已全部就绪，但学生端仍只有教师登录入口与出题工作台——学生无法在移动端消费「布置 → 练习 → 错题 → 复习」主路径。需按原型（`stitch-prototypes/m/practice.html`、`review.html`、`wrong.html`）与设计文档 36/40 实现学生练习页、复习页与错题本前端，闭环学生端学习流程。

## What Changes

- **后端最小补充**：`_issue_tokens` 登录响应新增 `role_id`（业务实体 id，学生即 `Student.id`）。当前响应只有 `user_id`（account.id），而所有学生列表端点以 `student_id`（=`account.role_id`）作路径参数并 403 校验本人，前端无此值无法发起任何学生请求。
- **学生登录**：新增 `student-login.html`（doc 40 §2.6 布局），`auth.js` 增加角色分流——student 登录后进入练习页，teacher 仍进工作台，不再被 `TEACHER_ROLES` 门控拒于学生页外。
- **练习页 `practice.html`**（doc 40 §2.2 + 原型）：任务列表（待完成/已完成分组 + 计数）→ 答题界面（复用 `GET /api/exam/{id}/questions`，学生态已剥离答案）→ 提交批改（`POST /api/practice/submit`）→ 得分/正确率结果。
- **复习页 `review.html`**（doc 40 §2.4 + 原型）：统计区（待复习/今日已复习/已掌握）→ 今日待复习列表（等级标签 + 题目）→ 逐题判级提交（`POST /api/review/submit`）。
- **错题本页 `wrong.html`**（doc 40 §2.3 + 原型）：统计卡 + 展开式错题列表 → 生成变式题（`POST /api/wrong-questions/variants`）→ 训练会话（`POST /api/wrong-questions/train`）→ 标记已掌握（`POST /api/wrong-questions/{qid}/mastered`）。
- **ChemAPI 扩充**：`api.js` 新增 8 个 student 方法；`student_id` 取自登录响应 `role_id`，随请求注入。
- **底部 4-Tab 导航**：AI 助教 / 练习 / 错题 / 我的，跨三页一致；AI 助教与我的为占位页（非本次范围）。
- **演示种子数据**：为 `student_demo` 生成一份每日练习 + 若干答错记录 + 复习任务，使三页有真实数据可走查。

### 非目标（本次不做，列为后续变更）

- AI 助教对话（doc 40 §2.1）与个人报告/设置（doc 40 §2.5）——仅占位 tab。
- 家长端两页。
- 变式题 LLM 生成——后端已确定性抽样，LLM 留 v1.1。
- 学生端 KaTeX 之外的富交互（下拉刷新、骨架屏动效等非原型要求项）。

## Capabilities

### New Capabilities

- `student-auth`: 学生登录入口与角色分流——student 登录后路由至练习页，登录响应携带业务实体 id 供前端作 student_id 调用各列表端点。
- `student-practice`: 学生练习页——待完成/已完成任务列表、答题界面（复用读卷接口）、提交批改与得分/正确率展示。
- `student-review`: 学生复习中心——待复习/已掌握统计、今日待复习列表、逐题判级提交与等级展示。
- `student-wrong-question`: 学生错题本——错题展开列表、变式题生成、训练会话判分、标记已掌握。

### Modified Capabilities

- `auth`: 统一账户登录响应 SHALL 增加 `role_id` 字段（业务实体 id），供前端学生端解析 student_id。

## Impact

- `chemai-backend/app/api/v1/auth.py`：`_issue_tokens` 返回体加 `role_id`（教师/家长同样受益，非 BREAKING，仅新增字段）。
- `chemai-backend/frontend/pages/js/api.js`：新增 8 个 student 方法（tasks / submit / effect / review-tasks / review-submit / wrong-list / variants / train / mastered）。
- `chemai-backend/frontend/pages/js/auth.js`：角色分流（student / teacher），`saveSession` 存 `student_id`。
- 新增页面：`frontend/pages/student-login.html`、`frontend/pages/practice.html`、`frontend/pages/review.html`、`frontend/pages/wrong.html`（由 FastAPI StaticFiles 已挂载的 `/pages` 服务）。
- 种子脚本：扩展 `scripts/seed_teacher.py` 或新增 `scripts/seed_student_demo.py`，为 `student_demo` 生成练习/作答/复习数据。
- 测试：`tests/unit/test_auth_api.py` 断言登录响应含 `role_id`。
- 前端参考：`stitch-prototypes/m/{practice,review,wrong}.html` 原型、设计文档 36（色板/字体/组件）与 40（页面规格）。
- 提交规范：Conventional Commits，scope 用 `ui`。
