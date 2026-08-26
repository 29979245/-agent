## 1. 后端：登录响应补 role_id

- [x] 1.1 在 `app/api/v1/auth.py` 的 `_issue_tokens` 返回体增加 `"role_id": account.role_id`；验证 `pytest tests/unit/test_auth_api.py --tb=short -x` 全绿
- [x] 1.2 更新 `tests/unit/test_auth_api.py`：断言登录成功响应含 `role_id`，且 student 场景下 `role_id` 等于该学生的 `Student.id`；验证新增断言通过

## 2. 前端基础设施：auth.js 分流 + api.js 学生方法

- [x] 2.1 `frontend/pages/js/auth.js`：`saveSession` 持久化 `role_id`；新增 `ChemAuth.isStudent()` 与 `ChemAuth.getStudentId()`（从 `chemai_user.role_id` 读取）；验证登录后 localStorage 含 role_id、方法返回值正确
- [x] 2.2 `auth.js` 登录后角色分流：`student` → 跳 `practice.html`，教师角色仍跳工作台 `exam-v2.html`；验证用 `student_demo` 登录落到练习页、`teacher_demo` 落到工作台
- [x] 2.3 `frontend/pages/js/api.js`：新增 `getExamQuestions(exam_id)`（若教师侧已存在则复用）与 9 个学生方法（tasks/submit/effect/review-tasks/review-submit/wrong-list/variants/train/mastered），URL 与载荷对齐 D6 契约；验证方法存在、请求路径与 body 正确（静态检查 + 冒烟抓包）

## 3. 学生登录页

- [x] 3.1 新建 `frontend/pages/student-login.html`（doc 40 §2.6 布局：Logo 区 + 学号/密码表单 + 全宽登录按钮 + 错误提示）；验证浏览器渲染、符合设计 tokens 与 430px 移动布局
- [x] 3.2 登录逻辑：提交 `/api/auth/login`，成功按角色路由，失败输入框抖动 + "账号或密码错误"；验证 `student_demo/demo123` 登录进入 `practice.html`，错误密码抖动提示

## 4. 练习页 practice.html

- [x] 4.1 任务列表视图：`studentPracticeTasks(student_id)` 拉取，按待完成/已完成分组 + 顶部两组计数 + 任务卡（标题/知识点 chip/难度/题数/按钮 开始或继续）；验证 `student_demo` 显示种子任务与正确计数
- [x] 4.2 答题视图：进入任务 → `getExamQuestions(exam_id)` 取题（学生态无答案）→ 逐题作答、上一题/下一题导航、切题保留已选、末题出现提交按钮；验证界面不显示正确答案/解析
- [x] 4.3 提交与结果：`studentPracticeSubmit(practice_id, answers)` → 展示得分/总题数/正确率/逐题判定；提交失败保留已选可重试；验证提交后结果正确、失败路径保留答案
- [x] 4.4 空态/错误态：无任务显示"暂无练习任务"；401 跳 `student-login.html`、403 提示无权限；验证三种状态

## 5. 复习页 review.html

- [x] 5.1 统计区 + 待复习列表：`studentReviewTasks(student_id)` 拉取 → 待复习/今日已复习/已掌握统计 + 按到期升序任务卡（题目 + 等级标签 + 到期信息）；空态"暂无待复习题目"；验证
- [x] 5.2 判级提交：答对/答错按钮 → `studentReviewSubmit(review_task_id, passed)` → 刷新该任务等级/状态，已掌握任务移入"今日已完成"并半透明；提交失败提示重试；验证等级升降与完成态

## 6. 错题页 wrong.html

- [x] 6.1 错题列表：`studentWrongQuestions(student_id)` → 统计卡（总错题/本周新增/已掌握）+ 展开式错题卡（知识点/难度/累计答错次数，展开显示详情与操作）；空态"暂无错题，继续保持！"；验证
- [x] 6.2 变式题训练：展开卡"生成变式题" → `studentWrongVariants(question_id, count)` → 训练答题视图 → `studentWrongTrain(student_id, answers)` 逐题判定并同步复习任务；抽样不足不阻断训练；验证
- [x] 6.3 标记已掌握："已掌握"按钮 → `studentWrongMastered(question_id, student_id)` → 该题从列表移除、已掌握计数 +1；非本人错题 403 提示且列表不变；验证

## 7. 占位页、导航与种子数据

- [x] 7.1 底部 4-tab tabbar 跨页共享（AI助教/练习/错题/我的，激活态区分）：练习 tab → practice.html、错题 tab → wrong.html；AI助教/我的为轻量占位页，我的占位页含「错题本/复习中心」功能入口列表，错题页顶部含"复习中心"入口；验证四页导航与激活态
- [x] 7.2 新建幂等脚本 `scripts/seed_student_demo.py`：复用 daily/adaptive 服务为 `student_demo` 生成一份 `mode=daily` 练习记录，落若干 `StudentAnswer`（含答错，经 `WrongQuestionTrainer`/`SpacedRepetitionEngine` 同步 `ReviewTask`）；重复执行安全；验证运行后三页均有真实数据
- [x] 7.3 前端冒烟：启动后端 + 运行种子后，以 `student_demo` 登录走通 练习（任务→答题→提交）→ 错题（列表→训练→已掌握）→ 复习（列表→判级）三页；验证浏览器走查无 console 错误、化学式 KaTeX/mhchem 正常渲染
