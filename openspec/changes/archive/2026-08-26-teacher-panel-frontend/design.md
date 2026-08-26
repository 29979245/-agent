## Context

后端学情能力已闭环：`/api/panel`（ClassLearningPanel 聚合、趋势、学生详情）、`/api/diagnosis`、`/api/classes`、`/api/warning` 全部就绪且有集成测试；但教师端无任何消费页面。原型 `stitch-prototypes/pages/teacher.html` 与 `students.html` 已定稿布局与交互（手写 CSS + Google Fonts + 原生 SVG/DOM 图表）。现状前端页面（exam-v2.html）为独立 HTML：内嵌 `<style>`、vendored 字体（Cormorant Garamond + IBM Plex Sans 已在 `vendor/fonts/`）、`js/api.js` + `js/auth.js`。本变更落地两个新页面、给 exam-v2 加导航条，并补齐页面依赖但后端尚缺的数据。

## Goals / Non-Goals

**Goals:**
- 新增 `panel.html`（班级学情面板）与 `students.html`（学生管理）两个真实页面，消费后端 API。
- 补齐 5 项后端缺口：学生个人成绩趋势、年级均分趋势、`knowledge_mastery`、学生活跃状态、`/api/classes` 教师隔离。
- exam-v2 头部加导航条直达两页面；login 落点不变。
- 新页面沿用现有本地 CSS + vendored 字体模式，离线可用、零新增依赖、图表原生 DOM/SVG。

**Non-Goals:**
- 预警列表前端页面（`/api/warning` 已有页面消费方时再补）。
- 教师主页/统一导航骨架。
- `student_no` 学号字段的数据模型变更与迁移（抽屉学号映射到 `student.id`）。
- 时间范围筛选的后端参数（工具栏时间选择器本次为静态控件）。
- 班级对比视图 / 年级基准线周更。

## Decisions

### D1 页面结构：独立 HTML + 客户端渲染

两个新页面为独立 HTML（非 SPA），复用 `js/api.js` + `js/auth.js`。`auth.js.requireAuth()` 门控 + `isTeacherLike()` 拦截学生/家长；数据加载用原生 JS（无 Vue），与原型一致。login 落点保持 `redirectAfterLogin() → exam-v2.html` 不变。

**备选**：Vue3 局部渲染（exam-v2 同款）——但面板/学生页为展示型页面，原生 JS 足够，少引一个运行时。

### D2 样式基线：本地 CSS + vendored 字体

页面内嵌 `<style>`，采用与 exam-v2 相同的 `@font-face`（`vendor/fonts/` 下 Cormorant Garamond + IBM Plex Sans）与设计令牌 `--oxford/--teal/--paper` 等（36-设计系统）。原型的手写 CSS 直接迁移为本地 CSS（去掉 Google Fonts 依赖，改用 vendored）。**不用 Tailwind CDN**（与现有页面一致，离线可用）。

**备选**：Tailwind CDN（记忆中的硬约束）——与现有页面实际代码不符，弃用。

### D3 图表：原生 DOM/SVG，零图表库

柱状图用 div 高度 + 百分比刻度；环形图用 SVG circle stroke 三段（或 conic-gradient）；双线趋势与迷你折线用 SVG polyline + 圆点。全部离线、可被 CSS 主题控制，与原型渲染一致。

### D4 面板数据流：三个并行请求 + 前端换算

`panel.html` 加载时并行请求：
1. `GET /api/panel/class/{cid}` → KPI、柱状图、环形图（`barrier_distribution` 计数→百分比）、类趋势线、班级平均分。
2. `GET /api/classes/{cid}/students` → 关注学生 Top5（取 `barrier_profile` 最高维度占比降序，>0 才入列）与每个学生的紧凑趋势迷你条。
3. `GET /api/panel/grade/{grade_id}/trend` → 年级线。

双线趋势按原型策略：两条独立 series 共享 N 个 X 槽位（类线与年级线的考试日期不必一一对应，各自按序铺点）。

### D5 学生页数据流：列表一次加载 + 抽屉按需

`students.html` 加载时请求学生列表（含 `score_trend`、`is_active`、`barrier_profile`）与班级面板 overview（班级平均分 KPI）。统计栏/筛选/分页（每页 20）全部客户端完成。点击卡片才请求 `GET /api/panel/class/{cid}/student/{sid}` 填抽屉（`weak_knowledge_points`、`score_trend`、`barrier_profile`）。深链 `?student=<name>` 自动开抽屉（沿用原型逻辑）。

### D6 后端补齐：复用现有聚合纯函数

- **`knowledge_mastery`**：在 `build_class_panel` 内由 `knowledge_point_error_rates` 的 total/errors 累计算出 `正确数/总作答数` 百分比；无作答为 `null`。修改 `class_overview` 输出。
- **学生个人趋势**：新增辅助 `_student_exam_accuracy_points(db, student_id)`（复用 `_exam_accuracy_points` 按学生过滤的变体：学生所有 `exam_type=exam` 且已发布/完成的作答，按考试聚合正确率）。`load_student_detail` 增 `score_trend`（最多 10 点）；`class_students` items 增 `score_trend` 紧凑形态（最近 6 点，避免列表 payload 膨胀）。
- **年级均分趋势**：新增 `GET /api/panel/grade/{grade_id}/trend`：取该年级全部班级的正式考试作答，按 `exam_date` 归组算正确率 → `trend` 数组（最多 10 点）。权限沿用 `analysis/read` + `_ensure_not_student`。
- **学生活跃状态**：`class_students` items 增 `is_active`（最近作答 `answered_at` 距今 ≤ 7 天；从未作答按 `created_at` 判定）与 `last_exercise_at`。聚合用一次 `GROUP BY student_id` 的 `MAX(answered_at)` 查询。
- **`/api/classes` 教师隔离**：`list_classes` 中 role==`teacher` 时经 `Class→Grade→school_id` 过滤本校；其余教师类角色全量。复用 warning/pending 的组织链过滤模式。

### D7 学号

抽屉"学号"显示 `student.id`（系统学生 ID），不新增字段。真实学号（如 2026301023）需后续 `student_no` 数据模型变更。

## Risks / Trade-offs

- `build_class_panel` 增 `knowledge_mastery` 字段可能使现有 panel 单测在精确 dict 断言下失败 → 跑回归，必要时同步更新断言（新增键不应破坏读取型断言）。
- `/api/classes` 隔离是 **BREAKING**：exam-v2 的班级下拉（`getClasses`）从全校变为本校 → 回归验证教师场景，补隔离集成测试。
- 学生列表一次返回全量（含紧凑趋势/活跃字段），单班 ≤50 学生，payload 可接受；未来学生量大需后端分页（本次不做）。
- 年级趋势端点跨年级班级聚合，SQL 稍重 → 单校班级规模小，先聚合后分页足够。
- 前端原生 JS 无测试基建 → 用现有回归脚本模式（若存在）手动验证；后端缺口均有集成测试兜底。

## Migration Plan

无 DB schema 变更（活跃状态、趋势、掌握率均为聚合推导；学号用现有 `id`）。后端改完先跑 `pytest tests/` 回归再提交；前端页面为新增文件，exam-v2 仅加静态导航条，无迁移步骤。

## Open Questions

- 时间范围选择器的后端参数（本次为静态控件，未接 API）——需确认后再接。
- 预警列表前端页是否随后单独成变更（`/api/warning` 已就绪）。
