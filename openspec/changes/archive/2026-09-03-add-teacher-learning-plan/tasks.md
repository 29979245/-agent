## 1. 后端：规则引擎生成器 + 落库/通知服务

- [x] 1.1 新建 `app/services/diagnosis/learning_plan.py`：`generate_plan(db, student_id)` 复用 `report_service.practice_answers`/`build_knowledge_points` 计算掌握度，薄弱点（mastery<0.6）按升序取前 3，结合 `barrier_profile` 主导障碍，输出 `{title, plan_text, plan_data.days}` 格式；无作答数据返回空计划+提示。验证：新增 `tests/unit/test_learning_plan.py` 单测（薄弱点排序、格式结构、无数据兜底）通过。
- [x] 1.2 同文件 `apply_plan(db, student_id, plan)`：写 `student.learning_plan` + 查 `StudentParentBinding(status=active)` 创建 `ParentNotification(learning_plan)`，单事务提交。验证：单测覆盖落库与通知创建。
- [x] 1.3 重构 `tools_diagnosis.send_learning_plan` 复用 `apply_plan`（消除重复查询/通知）。验证：`tests/unit/test_agent_tools_diagnosis.py` 既有用例全绿。

## 2. 后端：三个 REST 端点

- [x] 2.1 `app/api/v1/diagnosis.py` 新增端点：`POST /learning-plan/generate`（body {student_id}，`diagnosis:create`，返回预览不落库）、`POST /learning-plan/apply/{student_id}`（body {plan}，`diagnosis:create`，调 `apply_plan`）、`GET /learning-plan/{student_id}`（`diagnosis:read`，student 仅读自身他人 403）。验证：路由注册后 `python -c "from app.api.v1.diagnosis import diagnosis_router; ..."` 列出三个路由。
- [x] 2.2 端点单测（`tests/unit/test_learning_plan.py` 或并入 test_diagnosis_api）：teacher 可 generate/apply、student 调 generate/apply → 403、get 学生访问他人 → 403、apply 后 `student.learning_plan` 更新且家长通知落库。验证：`pytest tests/unit/test_learning_plan.py -q` 全绿。

## 3. 教师前端：学生管理抽屉生成/推送

- [x] 3.1 `frontend/pages/js/api.js` 新增 `generateLearningPlan(studentId)` / `applyLearningPlan(studentId, plan)`（挂 `/api/diagnosis/learning-plan/*`）。验证：`node --check js/api.js` 通过。
- [x] 3.2 `frontend/pages/students.html` 启用「生成学习计划」按钮：点击 → generate → 抽屉 `#planPreview` 渲染预览（plan_text Markdown + plan_data.days 逐日列表）+「推送给学生」确认 → apply → toast；409/无数据显示「暂无足够学习数据，无法生成计划」。验证：教师端浏览器手动走通生成→推送；`node --check` 内联脚本通过。
- [x] 3.3 深链 `?focus=<student_id>&action=plan`：`loadAll().then` 后解析参数 → 打开对应学生抽屉并自动触发生成。验证：浏览器打开 `students.html?focus=<sid>&action=plan` 自动开抽屉并出预览。

## 4. 学生前端：空态文案与新版渲染

- [x] 4.1 `frontend/pages/profile.html` 空态文案「暂无学习计划，完成练习后可生成」→「暂无学习计划，等待老师为你生成」。验证：学生端打开学习计划弹窗（无计划）显示新文案。
- [x] 4.2 `profile.html` `renderPlan` 新增 `plan_text + plan_data` 分支（plan_text 经 `SA.renderMarkdown`、plan_data.days 逐日列表逐条渲染），保留字符串/数组兼容分支。验证：教师推送后学生端弹窗渲染标题+每日任务、无 JSON 原文；`node --check` 通过。

## 5. 收尾验证

- [x] 5.1 全量单测通过：`python -m pytest tests/unit/test_learning_plan.py tests/unit/test_diagnosis_api.py tests/unit/test_agent_tools_diagnosis.py tests/unit/test_report_api.py -q` 全绿。
- [x] 5.2 线上链路验证：登录教师 → `POST /api/diagnosis/learning-plan/generate`（demo 学生）→ `apply` → 登录学生 → 报告端点 `learning_plan` 含计划、学生端弹窗渲染；越权（学生调 generate）403。
