## Why

教师工作台缺少学情数据可视化中枢。障碍诊断、练习提交、考试批改等上游数据已就绪，但没有面向教师的聚合出口——教师无法从知识点/学生/时间三维查看班级学习状态，也无法识别需重点关注的学生。产品设计文档 31《学情分析与预警系统设计》已定义完整的班级学情面板需求，本 change 落地其学情面板部分（`/api/panel` 闭环）。

## What Changes

- 新增学情聚合服务层 `services/analytics/`：知识点错误率、班级均分（指数衰减加权）、障碍分布（主导障碍计数）、成绩趋势等聚合能力。
- 新增 `/api/panel` 路由模块（6 端点）：
  - `GET /api/panel/class/{class_id}` — 班级学情面板完整数据（ClassLearningPanel）
  - `GET /api/panel/class/{class_id}/knowledge/{knowledge_point}` — 按知识点查看班级错误率分布及出错学生
  - `GET /api/panel/class/{class_id}/student/{student_id}` — 按学生查看学习详情
  - `GET /api/panel/class/{class_id}/trend` — 班级学情时间趋势
  - `GET /api/panel/export/{class_id}` — 导出班级学情报告（PDF，复用已有导出能力）
  - `GET /api/panel/dashboard/{teacher_id}` — 教师首页概览
- 新增 `GET /api/classes/{class_id}/students` 班级学生列表端点（面板渲染 KPI 与重点关注横条所需）。
- 新增 `ClassLearningPanel` 响应模型（见 design.md）。
- 权限：面板端点仅教师角色可见（admin / dept_admin / subject_lead / teacher），复用现有权限矩阵。

## Capabilities

### New Capabilities
- `learning-analytics`: 班级学情面板——学情聚合服务、`/api/panel` 端点、班级学生列表端点、ClassLearningPanel 响应模型。

### Modified Capabilities
<!-- 无现有 spec 的 REQUIREMENTS 发生变化：本 change 不修改既有能力的行为。 -->

## Impact

- 后端新文件：`app/api/v1/panel.py`、`app/services/analytics/panel_service.py`（或等价聚合模块）。
- 后端修改：`app/main.py`（注册 panel 路由）、`app/api/v1/exam.py`（班级学生列表端点）、`app/api/v1/__init__.py`（如适用）。
- 数据源：`StudentAnswer`（answered_at / is_correct / barrier_type）、`ExamRecord`（exam_date / exam_type / status）、`Student`（barrier_profile）、`Question`（knowledge_points）。
- 复用：`services/diagnosis/aggregation.py`（normalize_profile / aggregate）、已有诊断 stats API（`GET /api/diagnosis/class/{id}/stats`）。
- 无新数据库模型、无 Alembic 迁移、无新依赖。
- 测试：`tests/unit/`（聚合纯函数）+ `tests/integration/`（API 端点行为、权限）。
