## Purpose

学生端「我的」页与「AI助教」页的前端行为规格：消费报告聚合/绑定码/改密码后端能力展示个人学习数据，并按下 doc 30 的 SSE 事件协议渲染 AI 对话流，后端能力缺失时优雅降级。

## Requirements

### Requirement: 我的页报告消费与渲染

系统 SHALL 使学生端「我的」页在加载时以当前学生 ID 调用 `GET /api/report/student/{student_id}` 聚合报告，并渲染个人信息卡（姓名、班级、家长绑定码）、学习统计三列（完成练习数、正确率、连续打卡天数）、功能入口列表与学习周报弹窗；报告读取失败或未登录时不得展示错误数据。

#### Scenario: 本人加载报告成功

- **WHEN** 学生携带有效 token 进入「我的」页且后端返回正常报告
- **THEN** 个人信息卡展示 `profile.name` 与 `profile.class_name`，学习统计展示 `stats.completed_exercises`/`stats.accuracy`/`stats.streak_days`，功能入口列表完整渲染

#### Scenario: 报告数据为空

- **WHEN** 学生从未完成任何练习
- **THEN** 学习统计各指标显示为 0（正确率显示 0%），学习周报知识点掌握区为空态、教师评语不渲染

#### Scenario: 绑定码未生成

- **WHEN** `profile.bind_code` 为空字符串
- **THEN** 绑定码区域显示可生成的占位提示，点击可触发生成流程

#### Scenario: 未登录被拦截

- **WHEN** 无有效 token 的学生进入「我的」页
- **THEN** 页面重定向到学生登录页，不发起报告请求

### Requirement: 绑定码生成与复制

系统 SHALL 使「我的」页的家长绑定码可点击复制；当绑定码未生成时，点击 SHALL 调用 `POST /api/student/{student_id}/bind-code` 生成 6 位码并刷新显示；已有绑定码时重新点击 SHALL 覆盖旧码并更新显示。

#### Scenario: 复制绑定码

- **WHEN** 学生点击已有绑定码
- **THEN** 系统复制该码到剪贴板并短暂提示「已复制」

#### Scenario: 无绑定码生成

- **WHEN** 学生点击未生成状态的绑定码区域
- **THEN** 系统调用生成端点并以返回的新码更新显示

#### Scenario: 重新生成覆盖旧码

- **WHEN** 学生再次点击已有绑定码的生成入口
- **THEN** 系统返回新的 6 位码并覆盖页面显示的旧码

#### Scenario: 生成失败提示

- **WHEN** 生成绑定码请求失败（网络或权限错误）
- **THEN** 页面展示失败提示，不修改当前绑定码显示

### Requirement: 修改密码

系统 SHALL 使「我的」页「个人设置」入口提供修改密码弹窗：校验旧密码与新密码一致性后调用 `POST /api/auth/change-password`；成功修改后清空本地会话并返回学生登录页；失败时展示后端返回的错误信息。

#### Scenario: 修改成功

- **WHEN** 学生提交正确旧密码且两次新密码一致、后端校验通过
- **THEN** 系统提示修改成功、清除本地 token 并跳转学生登录页

#### Scenario: 新密码不一致

- **WHEN** 学生两次输入的新密码不一致
- **THEN** 系统前端拦截并提示两次新密码不一致，不发起请求

#### Scenario: 旧密码错误

- **WHEN** 学生提交的旧密码错误
- **THEN** 系统展示后端返回的业务规则冲突错误信息，密码不更新

#### Scenario: 必填校验

- **WHEN** 学生未填写旧密码或新密码即提交
- **THEN** 系统前端拦截并提示必填项，不发起请求

### Requirement: 学习周报与学习计划展示

系统 SHALL 使「我的」页「学习报告」入口以底部滑出弹窗展示学习周报：周标签、本周练习/正确率/学习时长概览、知识点掌握度条形图（按 `weekly.knowledge_points` 的 `name`/`mastery`）与教师评语；「学习计划」入口展示 `learning_plan` 计划内容，无计划时显示空态。学习计划 SHALL 支持新版 `plan_text + plan_data` 格式（标题 + 每日任务列表）与既有字符串/数组格式渲染。

#### Scenario: 周报弹窗渲染

- **WHEN** 学生点击「学习报告」且后端返回含知识点掌握度的周报
- **THEN** 弹窗展示周标签与概览数字，知识点按 `mastery` 比例渲染条形图，教师评语字段展示（为 null 时显示占位）

#### Scenario: 无学习计划空态

- **WHEN** `learning_plan` 为 `null`
- **THEN** 「学习计划」弹窗显示空态文案「暂无学习计划，等待老师为你生成」，不得暗示完成后自动生成

#### Scenario: 新版计划格式渲染

- **WHEN** `learning_plan` 为含 `plan_text` 与 `plan_data` 的对象（plan_data 含每日任务列表）
- **THEN** 弹窗渲染计划标题与每日任务列表（plan_text 经 Markdown 渲染、逐日任务逐条展示），不出现 JSON 原文

#### Scenario: 旧版计划格式兼容

- **WHEN** `learning_plan` 为字符串或字符串数组
- **THEN** 弹窗仍按既有格式渲染（字符串直接展示 / 数组逐条展示）

### Requirement: AI助教页布局与基础交互

系统 SHALL 提供 AI 助教页面：顶部标题栏（汉堡菜单 + 「ChemAI 助教」 + 新建对话按钮）、280px 左侧滑出抽屉（学生姓名/班级、历史对话列表、退出登录）、对话区（AI 气泡左对齐 Teal 浅色、用户气泡右对齐白底）、横向可滚动快捷芯片行、底部输入区与 4-tab 导航。发送消息 SHALL 追加用户气泡并展示加载态。

#### Scenario: 页面分区渲染

- **WHEN** 学生进入 AI 助教页且登录态有效
- **THEN** 标题栏、抽屉、对话区、快捷芯片、输入区与 tabbar 全部渲染，底部 tab「AI助教」为激活态

#### Scenario: 快捷芯片快捷发送

- **WHEN** 学生点击任一快捷芯片
- **THEN** 芯片文本填入输入框（或直接发送），与手动输入行为一致

#### Scenario: 发送消息

- **WHEN** 学生在输入框输入内容并点击发送
- **THEN** 对话区追加一条用户气泡，输入框清空，进入等待回复的加载态

#### Scenario: 抽屉与退出登录

- **WHEN** 学生点击汉堡菜单后点「退出登录」
- **THEN** 清空本地会话并跳转学生登录页

#### Scenario: 新建对话

- **WHEN** 学生点击标题栏新建对话按钮
- **THEN** 对话区清空为欢迎语空态（含快捷芯片与输入框）

### Requirement: SSE 对话流消费

系统 SHALL 以 `POST` + SSE 方式消费 AI 对话流：按 doc 30 §13 事件协议处理 `phase`（thinking/executing/planning/reply/awaiting_approval 阶段指示）、`text`（逐 token 追加到当前 AI 气泡并以 Markdown + KaTeX 渲染化学式）、`tool_call`/`tool_result`（工具调用与结果卡片，可收起）、`done`（结束加载态）事件；文本渲染经 XSS 净化处理。

#### Scenario: 文本增量渲染

- **WHEN** 对话流推送 `text` 事件
- **THEN** 系统将 `content` 追加到当前 AI 气泡并实时渲染，Markdown 与化学公式正确显示

#### Scenario: 阶段指示

- **WHEN** 对话流推送 `phase` 事件
- **THEN** 系统在对话区展示对应阶段指示（如「分析中/执行中/回复中」）

#### Scenario: 对话结束

- **WHEN** 对话流推送 `done` 事件
- **THEN** 系统结束加载态，输入框恢复可发送

#### Scenario: 工具调用渲染

- **WHEN** 对话流推送 `tool_call` 与 `tool_result` 事件
- **THEN** 系统展示工具调用卡片，卡片内容经 XSS 净化后渲染

### Requirement: AI后端缺失优雅降级

系统 SHALL 在 AI 对话后端端点不可用（端点返回 404、网络不可达或流中断）时优雅降级：对话区展示「AI 助教即将上线」类提示与重试入口，不产生未捕获错误；页面其余功能（tab 切换、抽屉）保持可用。

#### Scenario: 端点 404 降级

- **WHEN** 发送消息但 AI 对话端点不存在（404）
- **THEN** 系统结束加载态并在对话区展示「即将上线」降级提示，可点击重试

#### Scenario: 连接失败重试

- **WHEN** 发送消息但网络不可达或流中断
- **THEN** 系统展示「连接失败，点击重试」错误态，点击后重新发起对话

#### Scenario: 历史对话占位

- **WHEN** 打开侧边抽屉且后端无历史对话能力
- **THEN** 历史对话列表展示静态占位条目（或空态），不因后端缺失而报错

### Requirement: 前端 API 封装

系统 SHALL 在前端 API 层提供报告、绑定码、修改密码与 AI 对话流的封装方法：报告/绑定码/改密码 SHALL 调用真实后端端点并注入 Bearer token；AI 对话流封装 SHALL 支持以 `POST` + SSE 消费、携带 Bearer token，并向调用方暴露阶段/文本/完成/错误事件回调与取消能力。

#### Scenario: 报告封装指向真实端点

- **WHEN** 页面调用报告封装方法
- **THEN** 请求携带当前 token 请求 `GET /api/report/student/{student_id}`，返回聚合报告 JSON

#### Scenario: 对话流封装错误回调

- **WHEN** AI 对话流封装遇到 404 或网络错误
- **THEN** 封装以可判断的错误回调通知调用方（含可降级标记），不抛未捕获异常
