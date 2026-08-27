## Context

学生端 6 页面中 4 页（登录/练习/错题/复习）已完成，本变更补齐剩余两页：`frontend/pages/profile.html`（我的）与 `frontend/pages/ai-tutor.html`（AI助教），外加 `frontend/pages/js/api.js` 4 个封装。后端能力已就绪：`GET /api/report/student/{id}`、`POST /api/student/{id}/bind-code`、`POST /api/auth/change-password`（student-supplement-apis 已提交）。AI 对话后端（`app/agents/` SSE 端点）尚未实现，前端需按 doc 30/35 的 SSE 协议预对接并优雅降级。共享基建已存在：`js/student-common.js`（登录守卫、4-tab `renderTabbar`、`renderMarkdown`+XSS 净化、`ChemKaTeX`、`toast`、`escapeHtml`、`emptyState`）与 `css/student.css`（`.stats/.stat`、`.menu/.menu-item`、`.chip`、`.card`、`.track`、`.tabbar-item` 等）。

## Goals / Non-Goals

**Goals:**
- 我的页按原型 `report.html` 重建：个人信息卡、学习统计、功能入口、学习周报滑出弹窗、绑定码生成/复制、修改密码。
- AI 助教页按原型 `index.html` 重建：标题栏、抽屉、对话区、快捷芯片、输入区；按 SSE 协议消费对话流。
- 复用现有共享模块与设计系统，新增样式统一进 `student.css`。
- 后端 AI 端点缺失时页面可正常打开、可发送、可优雅提示，不产生未捕获错误。

**Non-Goals:**
- 不实现 Agent SSE 后端（`app/agents/` 另行排期）。
- 不做历史对话持久化（后端 conversation 端点在范围外，前端静态占位）。
- 不做附件拍照上传（doc 40 §2.1 提及，输入区保留附件图标占位，点击提示"即将上线"）。
- 不改动后端任何文件。

## Decisions

### D1: 样式追加到 student.css，而非页面内联
现有 4 页共用 `student.css`，`renderTabbar` 生成的 `.tabbar-item` 样式已集中。新增组件（`.profile-card`、`.bind-code`、`.stat-row`、`.menu-row`、`.sheet`/`.backdrop`、`.bubble.ai/.user`、`.drawer`、`.chat-input`、`.chip-row` 等）追加到 `student.css` 尾部。
- 备选：每页内联 `<style>`。→ 会复制设计与 4-tab 样式，与现有惯例冲突。
- 权衡：单文件增长但保持单一样式源，与现有 4 页一致。

### D2: api.js 用 fetch + ReadableStream 消费 SSE
`agentChatStream(payload, handlers)` 用 `fetch(url, {method:'POST', body, headers})` 拿 `response.body`，经 `TextDecoder` 按空行切分事件块、解析 `event:`/`data:` 行。
- 备选 A：`EventSource`——仅支持 GET，无法带 Bearer 与 POST body。排除。
- 备选 B：`XMLHttpRequest + onprogress`——可行但回调式、不支持 ReadableStream 干净取消。排除。
- 结论：fetch 流式是现代标准，支持 POST、Bearer、`AbortController` 取消。
- **事件名别名**：doc 30 §13 用 `phase/text`，doc 35 用 `thinking/token`。解析器将 `text`/`token` 视为文本别名、`phase`/`thinking` 视为阶段别名，前端对两套命名均可用。

### D3: 降级分级，页面按 kind 渲染
`agentChatStream` 的错误回调带 `{degradable, kind}`：
- `404`（端点未实现）→ `{degradable:true, kind:'not_implemented'}` → 页面渲染"AI 助教即将上线，敬请期待" + 重试。
- 网络不可达 → `{kind:'network'}` → "连接失败，点击重试"。
- 流中途中断 → `{kind:'stream'}` → "连接中断，点击重试"。
三种情况都结束加载态、恢复输入框。页面其余分区（tab、抽屉）不受影响。

### D4: 报告数据归一化
后端 `accuracy` 可能为 0-1 比例或 0-100 数值，前端统一 `fmtAccuracy`：`v>1 ? v : v*100` 取整显示 `%`。`mastery`（0-1）直接乘 100 作条形宽度。`duration_hours` 显示 `X 小时`（保留 1 位）。`streak_days`/`completed_exercises` 原样整数显示。`profile.class_name`、`bind_code` 缺省空字符串兜底。

### D5: 绑定码交互
- 有码：点击 code 复制（`navigator.clipboard`，失败回退 `document.execCommand('copy')`），toast「已复制」。
- 无码（空字符串）：点击触发 `generateBindCode(studentId)`，成功后更新显示；提供"重新生成"小按钮，覆盖旧码。
- 生成失败：toast 展示后端错误（`extractDetail`），不改显示。

### D6: 修改密码流程
个人设置弹窗内表单（旧密码/新密码/确认新密码）。前端校验：必填 + 两次新密码一致（不一致即拦截不发请求）。提交 `changePassword({old_password, new_password})`；成功 → `ChemAuth.logout()` + `redirectToLogin(true)` 回学生登录页；失败 → 弹窗内展示后端 `detail` 文案（旧密码错误等），表单保留。

### D7: AI 助教会话态
页面内维护 `threadId`（新建对话时生成 `conv_<ts>_<rand>`）；发送消息 → 追加用户气泡 + 新建 AI 气泡进入加载态 → SSE 流式填充。阶段指示以独立小标签显示在气泡上方（分析中/执行中/回复中）。Markdown 经 `renderMarkdown`（marked + sanitizeHtml）+ `ChemKaTeX.render`。快捷芯片 5 个：讲解知识点/配平方程式/做练习题/查看错题/总结学习，点击填入输入框。历史对话为静态占位列表，点击提示"对话记录即将上线"。

## Risks / Trade-offs

- [后端 SSE 事件命名不一致（doc 35 `token/thinking` vs doc 30 `text/phase`）] → D2 事件名别名兼容，后端实现后无需改前端。
- [后端 AI 端点未实现，SSE 链路无法真冒烟] → AI 页走降级路径验证；用临时 mock 事件源（本地脚本向页面注入模拟帧）验证流式渲染逻辑。
- [`accuracy` 口径不明（0-1 vs 0-100）] → D4 前端归一化，两种口径都正确显示。
- [`navigator.clipboard` 在非安全上下文不可用] → D5 回退方案 + toast 提示。
- [`learning_plan` 结构未定义（doc 40 仅提"每日任务列表"）] → 前端按对象友好渲染（遍历键值），避免强类型假设。

## Migration Plan

纯静态前端变更，无数据库/后端迁移。部署即刷新静态文件。回滚：还原上一版 `profile.html`/`ai-tutor.html`/`api.js`。验证：本地 `uvicorn app.main:app --port 8000` + seed 学生数据，走通 我的页 报告/绑定码/改密码；AI 页验证降级路径与 mock 流式渲染。

## Open Questions

- Agent SSE 端点的最终事件命名以后端实现为准（已用别名兼容，不阻塞本变更）。
- `learning_plan` 的精确展示形态可延后，前端先做通用渲染。
