## Purpose

提供班级学情数据聚合能力与教师端 `/api/panel` 查询端点，将诊断、练习、考试等上游数据聚合成教师可直观理解的三维学情视图（知识点 × 学生 × 时间），并支持按知识点/按学生下钻与教师首页概览。

## Requirements

### Requirement: 班级学情面板完整数据

系统 SHALL 提供 `GET /api/panel/class/{class_id}` 端点，返回单个班级的学情面板完整数据。响应 MUST 包含：

- `class_overview`：class_id、class_name、total_students、exam_count、avg_score_trend（按考试日期升序的班级平均正确率序列，最多 10 个数据点）、recent_exam_avg（0-100 数值，可空）、recent_exam_date（可空）。
- `knowledge_points`：按错误率降序的知识点列表，最多 10 个；每项含 knowledge_point、class_error_rate（0-100）、trend、related_barrier_distribution。
- `top_errors`：错误率最高的前 5 个知识点。
- `barrier_distribution`：concept / reading / expression 三类各对应一个整数人数（按主导障碍计数）。
- `top_improvers` 与 `top_declining`：进步/退步学生数组（可空）。

#### Scenario: 班级存在考试与作答数据

- **WHEN** 教师请求某班级的面板数据且该班级有已发布考试与作答记录
- **THEN** 响应包含完整 class_overview、按错误率降序的 knowledge_points、top_errors、barrier_distribution 与空或非空 top_improvers/top_declining

#### Scenario: 班级无任何作答数据

- **WHEN** 教师请求某班级的面板数据且该班级无任何作答记录
- **THEN** exam_count 为 0，avg_score_trend 为空数组，recent_exam_avg 与 recent_exam_date 为 null，knowledge_points 与 top_errors 为空数组，barrier_distribution 三类均为 0

### Requirement: 知识点错误率聚合

系统 SHALL 按公式 `E(kp, c) = errors(kp, c) / total(kp, c)` 计算班级在某一知识点上的错误率，其中 `errors` 为班级学生在该知识点题目上的答错总次数，`total` 为作答总次数（含正确与错误）。

#### Scenario: 知识点有作答记录

- **WHEN** 班级学生在一知识点标记的题目上有作答记录
- **THEN** 该知识点错误率按 答错次数 / 作答总次数 计算，以百分比返回

#### Scenario: 知识点从未被练习

- **WHEN** 班级学生在一知识点标记的题目上无任何作答（total = 0）
- **THEN** 该知识点不参与错误率降序排名，前端显示为"暂无数据"

#### Scenario: 一题多知识点

- **WHEN** 一道题目标记了多个知识点
- **THEN** 该题的一次作答对每个标记知识点各计一次（分别计入各知识点的 errors 与 total）

### Requirement: 班级均分指数衰减加权

系统 SHALL 使用指数衰减加权计算班级平均正确率，公式为 `Avg_weighted = SUM(w_i * accuracy_i) / SUM(w_i)`，其中 `w_i = exp(-ln2 * (t_now - t_i) / 604800)`，使一周前的数据权重衰减为当前的 50%。`recent_exam_avg` 取该加权值 ×100 的数值。

#### Scenario: 有多次考试数据

- **WHEN** 班级有多次考试的平均正确率记录
- **THEN** recent_exam_avg 按指数衰减加权计算，近期考试权重更高

#### Scenario: 单次异常低分

- **WHEN** 某次考试全班平均正确率异常低而其余考试正常
- **THEN** 该异常数据点因权重衰减不会过度拉低 recent_exam_avg

### Requirement: 障碍分布按主导障碍计数

系统 SHALL 为班级每个有画像的学生确定主导障碍类型（barrier_profile 中占比最高且大于 0 的维度），将学生归入 concept / reading / expression 三类之一计数，返回各类型整数人数。

#### Scenario: 有画像学生分布

- **WHEN** 班级学生存在 barrier_profile 且其中某一维度占比最高
- **THEN** 该学生计入对应障碍类型人数，barrier_distribution 三类人数之和等于班级有画像学生数

#### Scenario: 画像全零或缺失

- **WHEN** 学生 barrier_profile 缺失、为 null 或三维度均为 0
- **THEN** 该学生不计入任何障碍类型人数

### Requirement: 按知识点查看班级错误率

系统 SHALL 提供 `GET /api/panel/class/{class_id}/knowledge/{knowledge_point}` 端点，返回指定知识点的班级错误率分布及出错学生列表。

#### Scenario: 成功查询指定知识点

- **WHEN** 教师请求某班级在某知识点上的错误率分布
- **THEN** 响应包含该知识点的错误率与出错学生列表

#### Scenario: 权限校验失败

- **WHEN** 学生角色请求该端点
- **THEN** 返回 403

### Requirement: 按学生查看学习详情

系统 SHALL 提供 `GET /api/panel/class/{class_id}/student/{student_id}` 端点，返回指定学生的学情详情（错题历史、障碍类型、薄弱知识点）。

#### Scenario: 成功查询指定学生

- **WHEN** 教师请求某班级中某学生的学习详情
- **THEN** 响应包含该学生的错题历史、障碍类型分布与薄弱知识点

#### Scenario: 学生不属于该班级

- **WHEN** 教师请求的学生不属于该 class_id 对应班级
- **THEN** 返回 404

### Requirement: 班级成绩趋势

系统 SHALL 提供 `GET /api/panel/class/{class_id}/trend` 端点，返回班级学情随时间的变化（按时间维度的知识点错误率趋势或班级均分趋势）。

#### Scenario: 成功查询趋势

- **WHEN** 教师请求班级成绩趋势
- **THEN** 响应包含按时间排列的趋势数据点

#### Scenario: 无趋势数据

- **WHEN** 班级无足够的历史数据生成趋势
- **THEN** 返回空趋势数组，由前端兜底构建虚拟趋势

### Requirement: 教师首页概览

系统 SHALL 提供 `GET /api/panel/dashboard/{teacher_id}` 端点，返回教师所任教班级的概览数据（班级均分、练习统计、障碍分布）。

#### Scenario: 成功获取教师概览

- **WHEN** 教师请求自己的首页概览
- **THEN** 响应包含其任教班级的均分、练习统计与障碍分布

#### Scenario: 跨教师越权

- **WHEN** 教师请求其他教师的概览数据
- **THEN** 返回 403

### Requirement: 班级学情报告导出

系统 SHALL 提供 `GET /api/panel/export/{class_id}` 端点，导出当前班级的学情报告 PDF，且导出内容不包含其他班级信息。

#### Scenario: 成功导出

- **WHEN** 教师请求导出某班级学情报告
- **THEN** 返回该班级学情报告的 PDF 文件

#### Scenario: 学生角色导出

- **WHEN** 学生角色请求导出
- **THEN** 返回 403

### Requirement: 班级学生列表

系统 SHALL 提供 `GET /api/classes/{class_id}/students` 端点，返回指定班级的学生列表（含学生 id、姓名、barrier_profile），供面板渲染 KPI 与重点关注学生。

#### Scenario: 成功获取班级学生

- **WHEN** 教师请求某班级的学生列表
- **THEN** 响应包含该班级全部学生的 id、姓名与障碍画像

#### Scenario: 班级不存在

- **WHEN** 请求不存在的 class_id
- **THEN** 返回 404

### Requirement: 学情面板权限控制

系统 SHALL 限制学情面板端点仅对教师角色可见，学生与家长角色不可访问。教师角色含 admin / dept_admin / subject_lead / teacher。班级范围隔离遵循现有组织链模式：教师 MUST 仅可访问本校班级数据，admin 及以上角色不受班级范围限制。

#### Scenario: 教师访问本校班级

- **WHEN** 教师访问自己学校内的班级面板
- **THEN** 返回数据

#### Scenario: 教师访问他校班级

- **WHEN** 教师访问不属于其学校的班级面板数据
- **THEN** 返回 403 或 404

#### Scenario: 学生访问面板

- **WHEN** 学生角色访问任何面板端点
- **THEN** 返回 403
