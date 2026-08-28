## 1. js/api.js + js/auth.js 基础扩展

- [ ] 1.1 在 `js/api.js` 新增 `ChemAPI.parentLogin(phone, bind_code)`（POST `/api/parent/login`）并验证请求体与响应字段（access_token/refresh_token/user_id/role/name/school_id/role_id）与后端契约一致
- [ ] 1.2 在 `js/api.js` 新增家长系列方法：`children()`、`childReport(id)`、`childWeekly(id)`、`weeklyGenerate(id)`、`aiSummary(id)`、`bindChild(payload)`、`unbindChild(binding_id)`、`notifications(limit, offset)`、`markNotificationRead(id)`，逐一核对端点路径与 `ChemAPI.request` 用法
- [ ] 1.3 在 `js/auth.js` 为 `redirectAfterLogin` / `redirectToLogin` 增加 parent 分支，新增 `ChemAuth.isParent()`，验证 parent 登录后跳 `parent.html`、无令牌访问家长页跳 `parent-login.html`，且不影响既有 student/teacher 分支
- [ ] 1.4 在 `js/api.js`（或独立 util）建立 `error_code → 中文` 文案映射表（BIND_CODE_MISMATCH/BINDING_EXISTS/STUDENT_NOT_FOUND/网络失败等），验证映射覆盖本变更用到的全部错误码

## 2. parent-login.html 登录页

- [ ] 2.1 按原型创建 `parent-login.html`：品牌区 + 手机号/绑定码表单 + 绑定码说明卡片 + 底部信息，复用 `student.css` 设计令牌，验证页面结构与原型一致
- [ ] 2.2 实现绑定码输入过滤（仅字母数字、maxlength 6）与手机号输入过滤，验证输入非法字符被剥离
- [ ] 2.3 实现提交逻辑：调 `ChemAPI.parentLogin` → `ChemAuth.saveSession` 全字段 → 跳 `parent.html`；失败按映射表在表单区展示中文错误且不跳转
- [ ] 2.4 实现已登录会话提示：`ChemAuth.isParent()` 为真时显示"已有会话，直接进入"入口

## 3. parent.html 骨架 + 子女选择 + 绑定弹窗

- [ ] 3.1 按原型创建 `parent.html` 骨架：顶部标题栏（家长中心/设置/退出）、子女选择器（‹ 姓名 › + 绑定新子女入口）、三 Tab 导航、Tab 内容容器、浮动 AI FAB、底部抽屉，验证 DOM 结构与原型一致
- [ ] 3.2 实现页面控制器：`loadChildren()`（空数组 → 空态引导页；非空 → 默认选中第一个）、`loadCurrentTab()` 分派、子女切换重置并重载，验证切换子女刷新当前 Tab
- [ ] 3.3 实现未登录守卫：无有效家长令牌时 `redirectToLogin()`，验证无令牌访问跳登录页
- [ ] 3.4 实现"绑定新子女"弹窗：学号数字过滤 + 绑定码 6 位字母数字 + 关系下拉（监护人/其他），`bindChild` 提交，错误按 error_code 映射中文，成功刷新子女列表并加载新子女

## 4. 概览 Tab

- [ ] 4.1 实现统计卡渲染：本周练习 `weekly_exercises`、正确率 `weekly_accuracy`（百分比）、薄弱知识点 pill（`knowledge_points` mastery 最低者）、最近学习日期，验证字段与 `build_parent_report` 输出一致
- [ ] 4.2 实现最近学习时间线（`timeline[]` 最近 7 天行），验证空数组 → "暂无学习记录"空态
- [ ] 4.3 实现 Tab 加载失败错误态（映射文案 + 重试），验证接口失败时展示可读错误

## 5. 学习报告 Tab

- [ ] 5.1 实现学习概览 3 卡片（累计练习/累计答题/整体正确率），验证字段口径（overall 系列）正确
- [ ] 5.2 实现知识点掌握进度条（`knowledge_points[]` name + mastery 0-1 → 百分比宽度），验证 0 与 1 边界渲染
- [ ] 5.3 实现学习特点·家庭建议区块渲染 `learning_style`
- [ ] 5.4 实现 AI 解读折叠面板：展开调 `aiSummary(id)`，loading spinner → 文本展示；失败可读提示 + 重试，验证失败不展示半成品

## 6. 消息 Tab

- [ ] 6.1 实现通知列表渲染：`notifications(limit=20, offset=0)` 时间倒序，每项类型/标题/内容/时间/未读角标，验证与后端响应结构一致
- [ ] 6.2 实现滚动分页（offset+=limit 自动加载下一页，无更多即停）与"暂无消息"空态
- [ ] 6.3 实现未读点击标记已读（调 `markNotificationRead` 后本地置已读并刷新角标），验证请求不携带 parent_id
- [ ] 6.4 实现加载失败错误态 + 下拉刷新重试

## 7. 浮动 AI 助手

- [ ] 7.1 实现 FAB + 底部抽屉 + 5 个预置通俗化 chips，验证与原型 chips 文案一致
- [ ] 7.2 实现对话：`ChemAPI.agentChatStream`（SSE + `parseSSE`）流式增量渲染，验证消息追加显示
- [ ] 7.3 实现降级路径：对话通道失败/404 → 抽屉内可读提示，可关闭，不影响其余功能，验证降级后主面板仍可操作

## 8. 联调与回归

- [ ] 8.1 编写家长端 HTTP 级联通校验脚本（登录 → children → child report → notifications），对运行中 8000 端口跑通并记录结果
- [ ] 8.2 在浏览器手动走查：登录成功/失败、无子女空态、绑定新子女（成功/错误码）、三 Tab 渲染、AI 解读、通知已读、浮动 AI 降级，验证原型交互完整无回归
- [ ] 8.3 `graphify update .` 更新知识图谱，验证无语法报错
