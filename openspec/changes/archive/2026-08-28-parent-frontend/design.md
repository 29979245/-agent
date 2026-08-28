## Context

后端家长端 API（login / children / bind / child report / weekly / ai-summary / notifications）已全部闭环并归档，且由 `require_parent` / `require_bound_child` 守卫。前端目前 12 个静态页（`chemai-backend/frontend/pages/`）共用 `js/api.js`（`window.ChemAPI`，Bearer token 取自 `localStorage['chemai_token']`）与 `js/auth.js`（`window.ChemAuth` 登录/会话/跳转）以及 `student.css` 设计令牌（--oxford/--teal/--paper/--ink）。原型 `stitch-prototypes/m/parent-login.html`、`parent.html` 已定稿布局。动机见 proposal.md。

## Goals / Non-Goals

**Goals:**
- 2 个新页面（parent-login.html、parent.html）+ 最小扩展 `js/auth.js` / `js/api.js`，零后端改动
- 严格按原型布局；绑定码按已拍板决策收任意字母数字（后端 32 字符集不变）
- 绑定新子女弹窗收集学号 + 绑定码 + 关系
- 浮动 AI 通道不可用时优雅降级

**Non-Goals:**
- 不改后端任何端点/守卫/响应结构（响应字段名即契约，逐字使用）
- 不在本期实现周报页（后端 ready，前端无消费者；3 Tab 契约不含周报）
- 不引入新依赖；图表/动画用原生 DOM/CSS

## Decisions

**D1. 家长登录走独立方法，不动现有 login()**
`ChemAuth.login()` 面向 username/password。在 `js/api.js` 新增 `ChemAPI.parentLogin(phone, bind_code)` → `POST /api/parent/login`；登录页直接调用它并 `ChemAuth.saveSession(access_token, refresh_token, user_id, role, name, school_id, role_id)`。不把家长登录塞进现有 login() 避免破坏学生/教师流。
- 备选：给 login() 加 mode 参数 → 耦合两种登录形态，弃。

**D2. auth.js 增加 parent 分支（不改既有行为）**
- `redirectAfterLogin`：`role === 'parent'` → `parent.html`，其余走原逻辑。
- 新增 `ChemAuth.isParent()`：`role === 'parent'` 且令牌存在。
- `redirectToLogin`：parent → `parent-login.html`，其余走原逻辑。
- 既有 401 拦截（ChemAPI.request 内）复用：家长令牌失效 → 跳 parent-login。

**D3. parent.html 采用「子女状态 + 当前 Tab」单页控制器**
页面级 `state = { children: [], currentChildId: null, activeTab: 'overview', reportCache: {} }`。
- `loadChildren()` → `/children`：空数组 → 空态引导（含"绑定新子女"弹窗入口）；非空 → 默认选中第一个。
- `loadCurrentTab()` 按 activeTab 分派：overview/report/message 各一个加载函数。
- 子女切换：重置 activeTab 为 overview，清 reportCache，重新 load。
- 报告按 `currentChildId` 缓存（同一子女切换 Tab 不重复拉 report），子女切换即失效。

**D4. 绑定弹窗：数字学号 + 字母数字绑定码 + 关系下拉**
- 学号 `type=tel` 过滤非数字；绑定码过滤非字母数字、maxlength 6（对齐后端 32 字符集）。
- 关系映射 `ParentBindingRelation`：guardian=监护人，other=其他（提交英文码）。
- `POST /bind` 错误按 `error_code` 映射中文：`BIND_CODE_MISMATCH`/`BINDING_EXISTS`/`STUDENT_NOT_FOUND`；401 → 跳登录。成功 → `loadChildren()` 并把新子女设为当前。
- 后端已对"已存在 active 绑定"返回 `ConflictError`（HTTP 409，`error_code=BINDING_EXISTS`）——前端按 `error_code` 而非状态码取文案。

**D5. 统一错误文案映射**
前端建一张 `error_code → 中文` 表（放 `js/api.js` 或 parent 页内），覆盖登录与绑定、报告/通知加载失败。网络失败给"请检查网络后重试"。让错误可读，不对用户暴露原始 HTTP 状态码。

**D6. 概览 Tab 按原型渲染（timeline 用报告接口字段）**
- 统计卡：本周练习 `weekly_exercises`、正确率 `weekly_accuracy`（×100 四舍五入，拼 %）、薄弱知识点 pill（`knowledge_points` 中 mastery 最低者，name 字段）、最近学习 = 时间线最后一项日期。
- 时间线 `timeline[]` 渲染最近 ~7 天的"日期 / 对错数 / 正确率"行；空数组 → 空态"暂无学习记录"。

**D7. 学习报告 Tab 按原型渲染**
- 学习概览 3 卡：累计练习 `total_practices`、累计答题 `total_answers`、整体正确率 `overall_accuracy`（或按原型取 weekly）。
- 知识点进度条：`knowledge_points[]` 每项 name + mastery 百分比宽度条（0-1 换算 0-100%）。
- 学习特点·家庭建议：`learning_style` 区块。
- AI 解读：折叠面板，展开时 `POST .../ai-summary`，loading spinner → 文本 / 失败可读提示 + 重试。

**D8. 消息 Tab 分页 + 已读**
- `/notifications?limit=20&offset=0`，滚动到底自动加载下一页（offset+=limit），无更多即停。
- 未读通知带角标，点击即调 `PUT .../notifications/{id}/read` 后本地置已读并刷新角标。
- 请求不携带 parent_id（后端恒取自令牌）。
- 空态"暂无消息"；加载失败展示错误 + "下拉刷新"重试。

**D9. 浮动 AI 助手降级路径**
- FAB + 底部抽屉 + 5 个预置通俗化 chips；点击 chip 或输入后走 `ChemAPI.agentChatStream`（SSE，`parseSSE` 增量渲染）。
- 该后端端点当前未实现（404）→ 捕获请求失败/解析失败 → 抽屉内展示可读提示"AI 辅导暂不可用，请稍后再试"，可关闭，不影响其余功能。
- 备选：隐藏 FAB → 会丢原型预置交互，且通道恢复后仍需重启，弃。

## Risks / Trade-offs

- **[报告接口字段语义]** `overall_accuracy` 为累计口径、`weekly_accuracy` 为本周口径 → 原型概览卡取 weekly，学习报告卡取整体，语义不混用。落地时对照 `build_parent_report` 输出逐字段核对。
- **[AI 通道未实现]** 对话降级是预期路径，但若后续后端补齐，SSE 协议需与 `agentChatStream` 现状兼容 → 降级分支独立成函数，便于后端就绪后切换。
- **[bind 关系枚举]** 前端只展示两档（监护人/其他），若枚举扩容需同步 → 下拉选项集中定义一处常量。
- **[无自动测试]** 前端靠手动回归 → 复用既有 smoke 脚本模式，加一个家长端 HTTP 级联通校验（登录→children→report→notifications）。
