## Context

教师工作台缺少学情数据可视化出口。上游数据已就绪：`StudentAnswer`（answered_at / is_correct / barrier_type）、`ExamRecord`（exam_date / exam_type / status）、`Student`（barrier_profile / barrier_frozen）、`Question`（knowledge_points 逗号分隔）。诊断模块已有可复用能力：`services/diagnosis/aggregation.py` 的 `normalize_profile` / `aggregate`，以及 `GET /api/diagnosis/class/{id}/stats`（avg_profile / profiled_students，供环形图使用）。

两个现状约束塑造本设计：
- **无任教关系表**（`TeacherClassSubject` 仅在 CONTEXT.md 词条，代码未实现）——班级隔离实际走 school_id 组织链，参考诊断 API 的 `_require_student_in_teacher_school`。
- **权限矩阵中 student 也有 `analysis/read`**——面板端点需像诊断 stats 一样显式拒绝 student 角色。

## Goals / Non-Goals

**Goals:**
- 新增 `services/analytics/` 聚合服务，产出班级学情指标：知识点错误率、班级均分（指数衰减加权）、障碍分布（主导障碍计数）、成绩趋势。
- 新增 `/api/panel` 六端点 + `GET /api/classes/{class_id}/students`，权限复用现有矩阵与组织链隔离模式。
- 无新数据库模型、无 Alembic 迁移、无新依赖。

**Non-Goals:**
- `/api/analytics` 数据可视化工作台的 13+ 端点（后续独立 change）。
- 班级/年级对比、年级基准线、跨班级矩阵（后续独立 change）。
- 预警引擎（`WarningLog` / `EarlyWarningService` / `/api/warning`，独立 change）。
- 面板前端页面（本次仅后端 API）。

## Decisions

### D1. 聚合实现：批量 SQL 查询 + Python 内存聚合

单班数据量小（≤50 学生 × ≤千级作答），用 SQLAlchemy 一次性拉取班级学生的作答记录与题目知识点映射，在 Python 内存完成聚合。相比 SQL 聚合：可单测、无 SQLite/MySQL 方言差异、逻辑集中。知识点拆分用**预构建 `question_id → knowledge_points[]` 映射**（仅涉及本班题目），避免对 `knowledge_points` 字符串做 LIKE 全表扫描。

### D2. 知识点错误率聚合（对应 spec「知识点错误率聚合」）

`E(kp, c) = errors / total`。遍历本班全部作答，对每题按 `knowledge_points` 拆分，逐知识点累计 errors（is_correct=False）与 total（所有作答）。一题多知识点分别计（spec 已覆盖）。`total=0` 的知识点不参与降序排名。

### D3. 班级均分：仅聚合 `exam_type=exam`，指数衰减加权

`avg_score_trend` 按 `exam_date` 升序取**最近 10 次**正式考试（`exam_type=exam`，`status` 已发布/已完成），每次正确率 = 该考试作答 `is_correct` 平均。`recent_exam_avg` = `Avg_weighted × 100`，权重 `w_i = exp(-ln2·(t_now−t_i)/604800)`（一周前权重 50%）。

**为何排除 practice**：设计文档 §2.1 原文写"练习/考试"，但每日/自适应练习是 per-student ZPD 独立难度，同班学生分数不可横向比较，计入会造成"班级均分"失真。排除 practice 与预警 change 的 `score_drop` 口径（仅 exam）统一。差异见 Risks R1。

### D4. 障碍分布：主导障碍计数（对应 spec「障碍分布按主导障碍计数」）

遍历班级学生，`normalize_profile(s.barrier_profile)` 取占比最高且 >0 的维度归入 concept/reading/expression 三类，返回整数人数。画像缺失/全零学生不计入。环形图**不在此服务**——复用诊断 stats 的 `avg_profile`（百分比）。避免与已有聚合重复。

### D5. 权限与隔离（对应 spec「学情面板权限控制」）

- 端点级：`@require_permission("analysis", "read")` + 显式拒绝 student（因矩阵中 student 有 analysis/read），沿用诊断 `_ensure_not_student` 模式。
- 班级范围：教师 MUST 校验目标班级所属 school_id == 教师 school_id（沿用 `_require_student_in_teacher_school` 组织链模式）；admin/dept_admin/subject_lead 不限。
- dashboard 跨教师：仅本人或 admin+（沿用 `_require_own_config` 模式）。

### D6. 响应模型组织

`ClassLearningPanel` 响应模型定义在 API schema 层（非 DB 模型）。结构遵循 spec「班级学情面板完整数据」：class_overview / knowledge_points / top_errors / barrier_distribution / top_improvers / top_declining。

### D7. 趋势与导出

- `trend` 端点：按时间返回班级均分趋势（与 avg_score_trend 同源），无数据返回空数组（前端兜底虚拟趋势）。
- `export` 端点：复用现有 paper-export 的 HTML→PDF 栈（已注册 SimSun 中文字体），生成班级学情报告（overview + 知识点错误率 + 障碍分布表格），仅含当前班级数据。

## Risks / Trade-offs

- **[R1] avg_score_trend 仅 exam 与设计文档"练习/考试"表述差异** → 与预警 score_drop 口径统一，避免 practice 难度漂移导致均分失真；副作用是练习为主的数据集上均分序列稀疏。缓解：KPI"最近均分"空缺时前端已有"暂无数据"空态；若后续需要 practice 趋势，作为独立指标追加而非混入班级均分。
- **[R2] knowledge_points 是逗号分隔字符串，无标准化主键** → 聚合精度依赖题目知识点标注质量。缓解：聚合按字符串精确切分，标注一致即可，本次不引入知识点规范化。
- **[R3] 权限矩阵 student 有 analysis/read** → 若仅用矩阵判定会漏放行学生。缓解：端点统一走 `_ensure_not_student` 式显式拒绝，且集成测试覆盖学生 403。
- **[R4] 全历史聚合的查询开销** → 单班数据量小，Python 内存聚合 + question_id 映射避免 LIKE 扫描；若未来数据增长，可加 `student_answer.exam_id` 索引或改为按考试记录增量聚合（超出现阶段）。

## Migration Plan

无数据库迁移。新增文件 + `main.py` 路由注册即可，独立可部署。回滚：移除 panel 路由注册与 `classes/{class_id}/students` 端点，删除新增模块，不触碰既有行为。
