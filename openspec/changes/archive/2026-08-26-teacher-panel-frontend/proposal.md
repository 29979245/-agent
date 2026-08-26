## Why

预警引擎与学情面板后端（`/api/panel`、`/api/diagnosis`、`/api/warning`）已闭环，但教师端没有任何可视化页面消费这些数据——教师登录后直达出题工作台，无法看到班级学情、学生画像或预警。原型（`stitch-prototypes/pages/teacher.html`、`students.html`）已定稿布局与交互，需落地为真实页面并补齐原型依赖但后端尚缺的数据。

## What Changes

- **新增班级学情面板页面** `frontend/pages/panel.html`：班级选择器 + 4 个 KPI 卡片 + 知识点错误率柱状图 + 障碍类型分布环形图 + 班级/年级均分趋势折线图 + 关注学生横条（Top5），点击关注学生跳转学生管理并自动打开抽屉。
- **新增学生管理页面** `frontend/pages/students.html`：统计栏 + 搜索/筛选 + 学生卡片网格（分页）+ 右侧 480px 详情抽屉（障碍分布、成绩趋势迷你线、薄弱知识点标签）。
- **出题工作台加导航条** `frontend/pages/exam-v2.html`：顶部横条链接到学情面板/学生管理；login 仍直达 exam-v2。
- **扩展 `js/api.js`**：新增 panel / classes 学生列表方法。
- **后端补齐缺口**（前端渲染所必需）：
  - 学生个人成绩趋势数据（卡片 mini-bar 与抽屉迷你折线用）。
  - 年级平均分趋势（趋势图第二条线）。
  - 学生列表暴露活跃状态（活跃学生统计与筛选用）。
  - 面板响应新增 `knowledge_mastery`（知识点掌握率 KPI，解决原型与设计文档的 KPI 冲突）。
  - 修复 `GET /api/classes` 返回全校班级的越权问题，改为教师学校隔离。
- **BREAKING**：`GET /api/classes` 从返回全校班级改为仅返回教师本校班级（admin+ 仍全量）。

## Capabilities

### New Capabilities

- `teacher-panel-frontend`: 教师端学情面板 + 学生管理两个页面的行为，及其依赖的后端数据补齐（学生个人趋势、年级均分趋势、学生活跃状态、班级列表教师隔离）。

### Modified Capabilities

- 无（学情面板后端行为 `learning-analytics` 的主 spec 尚未建立——class-learning-panel 变更仍 active 未归档；本变更所有后端扩展并入新 capability `teacher-panel-frontend`，不与既有 active 变更冲突）。

## Impact

- **前端**：`chemai-backend/frontend/pages/panel.html`、`students.html`（新建）、`exam-v2.html`（加导航条）、`js/api.js`（加方法）、复用本地 CSS 与 vendored 字体（Cormorant Garamond + IBM Plex Sans）。
- **后端**：`app/services/analytics/panel_service.py`（学生趋势、年级均分、活跃状态聚合）、`app/api/v1/panel.py`（新端点）、`app/api/v1/exam.py`（`/api/classes` 教师隔离）、`app/db/models/org.py`（如活跃状态需新增字段/推导）。
- **权限**：新端点沿用 analysis/read + `_ensure_not_student` + school_id 组织链隔离。
- **测试**：后端扩展需 unit + integration 测试；前端无自动测试，靠手动/回归脚本验证。
- **依赖**：无新增依赖；图表用原生 SVG/DOM 绘制（与原型一致）。
