## 1. 后端：面板指标补齐（TDD）

- [ ] 1.1 `build_class_panel` 新增 `class_overview.knowledge_mastery`（正确作答数 / 标记知识点作答数 × 100，无作答为 `null`）；先写单元测试（有数据/无数据两例）→ 实现 → 跑 `pytest tests/unit/test_aggregation.py -x`
- [ ] 1.2 新增 `_student_exam_accuracy_points(db, student_id)` 辅助，`load_student_detail` 返回 `score_trend`（最多 10 点），`class_students` items 返回紧凑 `score_trend`（最近 6 点）；补单测/集成断言 → 跑 `pytest tests/unit/test_aggregation.py tests/integration/test_panel_api.py -x`
- [ ] 1.3 新增 `GET /api/panel/grade/{grade_id}/trend` 端点（年级全部班级正式考试按 `exam_date` 聚合正确率）；集成测试两例（有考试返回 trend、无考试返回空）→ 实现 → 跑 `pytest tests/integration/test_panel_api.py -x`
- [ ] 1.4 `class_students` items 新增 `is_active` 与 `last_exercise_at`（最近作答 ≤ 7 天为活跃，从未作答按 `created_at`，作答时间为空则 `null`）；集成测试覆盖活跃/超期/从未作答三例 → 实现 → 跑 `pytest tests/integration/test_panel_api.py -x`
- [ ] 1.5 `GET /api/classes` 教师学校隔离（role==teacher 仅本校，其余教师类角色全量）；集成测试：教师见本校/不见他校、admin 全量 → 实现 → 跑相关测试
- [ ] 1.6 全量回归 `pytest tests/ --tb=short`，确认无既有用例被新字段/隔离破坏

## 2. api.js 扩展

- [ ] 2.1 在 `js/api.js` 添加 `getClassPanel(classId)`、`getPanelTrend(classId)`、`getGradeTrend(gradeId)`、`getClassStudents(classId)`、`getStudentDetail(classId, studentId)` 方法（映射到 1.1-1.5 的端点）；验证：浏览器 `window.ChemAPI` 下方法存在且路径正确

## 3. 前端：面板页面结构与数据加载

- [ ] 3.1 新建 `frontend/pages/panel.html` 骨架：本地 CSS + 设计令牌（--oxford/--teal/--paper）+ 班级选择器 + 4 个 KPI 卡片（考试次数/关注学生/班级平均分/知识点掌握率）；接入三个并行请求（panel/students/grade-trend）；浏览器验证 KPI 数字与班级切换
- [ ] 3.2 班级切换后重载全部数据源；验证切换班级后 KPI 与各图表数据同步刷新

## 4. 前端：图表可视化

- [ ] 4.1 知识点错误率柱状图：div 高度 + 百分比刻度，按 `error_rate` 降序 ≤10 项，无数据隐藏；浏览器验证柱高与数值
- [ ] 4.2 障碍类型分布环形图：SVG 三段扇区，`barrier_distribution` 计数→百分比（概念紫/审题蓝/表述青），图例带占比；浏览器验证三段占比和≈100%
- [ ] 4.3 班级-年级双线趋势折线图：SVG polyline + 圆点，两条独立 series 共享 X 槽位（类线取 `avg_score_trend`、年级线取 grade trend），空数据显示"数据不足"；浏览器验证双线与 X 轴日期
- [ ] 4.4 验证三图数据口径：柱状图降序、环形图占比换算、趋势线点数与后端返回一致

## 5. 前端：关注学生横条

- [ ] 5.1 关注学生横条：Top5（`barrier_profile` 最高维度占比降序、>0 才入列）+ 主导障碍标签（概念紫/审题蓝/表述青）+ 近期趋势迷你条（紧凑 `score_trend`）；全部为 0 时隐藏整个区域；浏览器验证横滑与卡片内容
- [ ] 5.2 关注学生卡片点击跳转 `students.html?student=<姓名>`；验证深链跳转并自动打开对应抽屉

## 6. 前端：学生管理列表

- [ ] 6.1 新建 `frontend/pages/students.html`：统计栏（总人数/活跃学生/关注学生/班级平均分）+ 搜索框按姓名实时过滤 + 筛选 chip（班级/障碍类型/活跃状态）+ 学生卡片网格（姓名/班级/障碍 3 段条/趋势 dots）；浏览器验证统计与过滤
- [ ] 6.2 卡片网格每页 20 条分页（当前页高亮）+ 空态"未找到匹配的学生"；浏览器验证分页与空态

## 7. 前端：学生详情抽屉

- [ ] 7.1 抽屉结构：480px 右侧滑出 + 遮罩层 + ESC/遮罩关闭；内容含学号（`student.id`）/班级/活跃状态、障碍分布条形图、成绩趋势迷你折线图、薄弱知识点标签、操作按钮占位；浏览器验证开合
- [ ] 7.2 抽屉数据：按需请求 `getStudentDetail` 填充（薄弱点/趋势/障碍条）+ 深链 `?student=` 自动开抽屉；浏览器验证数据填充与深链

## 8. 前端：导航与联调

- [ ] 8.1 `exam-v2.html` 头部 `.page-header` 加静态导航链接（学情面板 → panel.html、学生管理 → students.html）；验证登录落点仍为 exam-v2、链接可达
- [ ] 8.2 浏览器手测全链路：教师登录 → exam-v2 导航 → 面板渲染 → 关注学生卡片跳转学生管理自动开抽屉 → 学生角色访问两页面被拦截（`isTeacherLike`）
