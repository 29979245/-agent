## Why

家长端后端（登录、绑定、报告、周报、AI 解读、通知）已全部闭环，但没有任何前端页面消费这些数据——家长只能通过教师口头了解子女学习情况。原型（`stitch-prototypes/m/parent-login.html`、`parent.html`）已定稿布局与交互，需落地为真实页面，让家长用通俗语言看到子女化学学习状态。

## What Changes

- **新增家长登录页** `frontend/pages/parent-login.html`：手机号 + 6 位绑定码登录 → `POST /api/parent/login`，绑定码输入接受任意字母数字（后端 32 字符集不变），错误码直接映射中文提示。
- **新增家长主面板** `frontend/pages/parent.html`：顶部标题栏 + 子女选择器（含"绑定新子女"弹窗）+ 3 Tab（概览 / 学习报告 / 消息）+ 浮动 AI 助手。
- **绑定新子女弹窗**：收集【子女学号 + 绑定码 + 关系】→ `POST /api/parent/bind`，后端业务错误码（绑定码不匹配/已存在绑定）映射中文提示。
- **概览 Tab**：本周练习 / 正确率 / 薄弱知识点 pill / 最近学习时间线（`/report` 的 `timeline` 数组）。
- **学习报告 Tab**：学习概览 3 卡片 + 知识点掌握进度条（`knowledge_points[].mastery`）+ 学习特点·家庭建议 + AI 解读折叠面板（`/report/ai-summary`）。
- **消息 Tab**：通知手风琴（`/notifications` 分页倒序 + 标记已读），空态"请先绑定学生"引导页，加载失败下拉重试。
- **扩展 `js/auth.js`**：`redirectAfterLogin` / `redirectToLogin` 增加 parent 分支（→ parent.html / parent-login.html）。
- **扩展 `js/api.js`**：新增 parentLogin 与家长系列方法（children / childReport / childWeekly / weeklyGenerate / aiSummary / notifications / markNotificationRead / bind / unbind）。

## Capabilities

### New Capabilities

- `parent-frontend`: 家长端两个页面的行为——独立登录、3 Tab 主面板、绑定新子女、浮动 AI 助手的降级路径。

### Modified Capabilities

- 无（家长端后端行为 `parent-portal` 已归档；本变更纯前端消费既有端点，不改后端行为）。

## Impact

- **前端**：`chemai-backend/frontend/pages/parent-login.html`、`parent.html`（新建）、`js/auth.js`（parent 分支）、`js/api.js`（家长方法）；复用本地 CSS 与 vendored 字体（Cormorant Garamond + IBM Plex Sans）与设计令牌。
- **后端**：无改动。
- **权限**：家长端点已由 `require_parent` / `require_bound_child` 守卫，前端仅携带令牌、不触碰他人数据。
- **测试**：前端无自动测试，靠手动/回归脚本验证（沿用既有 smoke 脚本思路）。
- **依赖**：无新增依赖；图表与动画用原生 DOM/CSS 绘制（与原型一致）。
