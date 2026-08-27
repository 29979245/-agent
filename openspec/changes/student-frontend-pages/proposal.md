## Why

doc 52 学生端 6 页面中，「我的」页（profile.html）与「AI助教」页（ai-tutor.html）仍是占位符（"建设中"），学生端主流程缺少个人报告查看、家长绑定、密码修改与 AI 对话能力。两页所需后端能力已就绪或按协议预留：报告聚合（`GET /api/report/student/{id}`）、绑定码（`POST /api/student/{id}/bind-code`）、改密码（`POST /api/auth/change-password`）均已上线；Agent SSE 对话后端按 doc 30 协议规划但尚未实现。本次补齐两个前端页面，完成学生端 6 页面闭环。

## What Changes

**Scope**: `ui`（纯前端——2 个静态页面 + 共享 JS/CSS 封装，无后端改动）。

- **重建 profile.html（我的页）**：按原型 `stitch-prototypes/m/report.html` 与 doc 40 §2.5 布局——顶部标题栏 + 个人信息卡（80px 头像、姓名、班级、家长绑定码可点击复制/重新生成）+ 学习统计三列（完成练习/正确率/连续打卡）+ 功能入口 5 项（学习报告/学习计划/我的错题本/复习中心/个人设置）+ 底部 4-tab。消费 `GET /api/report/student/{id}` 聚合报告（profile/stats/weekly/learning_plan）、`POST /api/student/{id}/bind-code` 生成绑定码、`POST /api/auth/change-password` 修改密码。学习周报以底部滑出弹窗展示（统计概览 + 知识点掌握度条形图 + 教师评语占位）。
- **构建 ai-tutor.html（AI助教页）**：按原型 `stitch-prototypes/m/index.html` 与 doc 40 §2.1 布局——顶部标题栏（汉堡菜单 + "ChemAI 助教" + 新建对话）+ 280px 侧边抽屉（学生信息 + 历史对话 + 退出登录）+ 对话区（AI 气泡 Teal #e0f2f1 左 / 用户气泡白右）+ 5 个快捷芯片 + 底部输入区 + 4-tab。按 doc 30 §13 SSE 事件协议以 `POST` + SSE 消费对话流（phase/text/tool_call/tool_result/done），Markdown + KaTeX 渲染化学式；后端端点缺失（404）时优雅降级为「即将上线」提示，不发中断错误。
- **api.js 新增封装**：`getStudentReport(studentId)`、`generateBindCode(studentId)`、`changePassword({old_password, new_password})`、`agentChatStream(payload, handlers)`（SSE 流式封装，供 AI 助教消费；失败时给出可判断的降级信号）。
- 补充共享样式与工具：`student.css` 新增卡片/统计/菜单/弹窗/聊天气泡等组件样式（沿用现有 4-tab 与设计系统色板）。

**不改动**：后端（含 Agent SSE 后端 `app/agents/`）不属于本次变更，另行排期。

## Capabilities

### New Capabilities

- `student-frontend`: 学生端「我的」页与「AI助教」页的前端行为规格——报告数据消费与渲染、绑定码生成/复制、密码修改、SSE 对话流渲染与后端缺失时的降级策略。

### Modified Capabilities

（无——现有 `report` / `student-account` 规格描述后端能力，本次仅新增前端消费方，不改后端行为。）

## Impact

- **前端文件**：
  - `frontend/pages/profile.html`（重建）
  - `frontend/pages/ai-tutor.html`（重建）
  - `frontend/pages/js/api.js`（新增 4 个封装）
  - `frontend/pages/js/student-common.js`（如需新增标签/格式化工具）
  - `frontend/pages/css/student.css`（新增组件样式）
- **无后端改动**；Agent SSE 后端、历史对话持久化、附件拍照上传属后续范围，本次以占位/降级处理。
- 依赖现有 `/api/report/student/{id}`、`/api/student/{id}/bind-code`、`/api/auth/change-password` 与登录态 `ChemAuth`（token / student_id）。
- 提交规范：Conventional Commits，scope 用 `ui`。
