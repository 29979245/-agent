## Purpose

诊断服务闭环：批量触发 LLM 深度诊断、将三维障碍判定持久化到学生作答与画像、提供班级/知识点/历史统计读取、教师覆盖与阈值配置，并对 `diagnosis` 资源实施权限控制，供下游学情聚合（doc 50）、自适应练习（doc 49）与 Agent 诊断工具（doc 57）消费。

## Requirements

### Requirement: run-llm 批量诊断触发
系统 SHALL 提供 `POST /api/diagnosis/run-llm` 端点：批量触发 LLM 深度诊断；SHALL 接受 exam_id 并收集该考试的错误作答，优先处理 `barrier_type IS NULL`（未诊断）的记录；单批处理 SHALL 不超过 10 条，超出部分提示分批；SHALL 使用 ThreadPoolExecutor(max_workers=5) 并发执行仅含纯 LLM IO 的诊断调用，线程池 SHALL 不执行任何数据库写操作；主线程 SHALL 统一收集结果、校验合法 JSON、单事务提交落库并触发画像聚合；执行完毕 SHALL 返回已诊断数、失败数与失败原因；单条失败的失败状态 SHALL 持久化到该作答（diagnosis_flag=error、diagnosis_detail 含原因），其余条目正常落库，下次 run-llm SHALL 对失败条目可重跑。

#### Scenario: 未诊断错误被优先处理
- **WHEN** 一批错误作答中同时存在已诊断与未诊断记录
- **THEN** 未诊断记录（barrier_type IS NULL）优先进入诊断队列

#### Scenario: 超过批量上限分批提示
- **WHEN** 单次提交错误作答超过 10 条
- **THEN** 本批仅处理前 10 条并提示剩余数量

#### Scenario: 单条 LLM 失败不中断整批
- **WHEN** 批量中某条 LLM 诊断重试耗尽返回错误信号
- **THEN** 该条标记为失败并记录原因，其余条目正常落库

#### Scenario: 无待诊断数据
- **WHEN** 指定考试无错误作答或全部已诊断
- **THEN** 返回提示信息且不触发任何 LLM 调用

### Requirement: 障碍画像聚合
系统 SHALL 提供五步画像聚合：查询该学生的已诊断错误作答 → 按 barrier_type 计数 → 归一化（除以错误总数，总数为 0 时以 1 计避免除零，百分比保留 2 位小数）→ 写入 Student.barrier_profile（补零保证 concept/reading/expression 三键齐全）→ 更新 Student.barrier_last_updated 时间戳；聚合 SHALL 在 run-llm 批量落库后或单条诊断持久化后自动触发。聚合 SHALL 跳过已冻结画像的学生（教师 override 覆盖后冻结），直到教师显式解除冻结。

#### Scenario: 无错误作答不除零
- **WHEN** 学生没有任何已诊断的错误作答
- **THEN** 归一化以 1 为分母，画像三键全为 0.00

#### Scenario: 三键补零
- **WHEN** 学生仅出现 concept 类错误、无 reading/expression 错误
- **THEN** barrier_profile 中 concept 为计算值，reading 与 expression 补 0.00

#### Scenario: 时间戳随画像更新
- **WHEN** 画像聚合成功写入
- **THEN** Student.barrier_last_updated 更新为当前时间

### Requirement: 班级障碍分布与统计读取
系统 SHALL 提供只读统计端点：`GET /api/diagnosis/barrier/{class_id}/{exam_id}` 返回该班级指定考试的三维障碍分布（concept/reading/expression 各人数与占比），exam_id 缺失或当前考试无数据时 SHALL 回退聚合班级学生的历史 barrier_profile；`GET /api/diagnosis/class/{id}/stats` 返回班级整体诊断统计；`GET /api/diagnosis/class/{id}/kp/{kp}` 返回指定知识点的障碍分布；`GET /api/diagnosis/history/{student_id}` 返回单个学生的诊断历史记录。

#### Scenario: 当前考试数据缺失回退历史
- **WHEN** 指定考试的作答数据不存在
- **THEN** 分布按班级学生历史 barrier_profile 聚合返回

#### Scenario: 知识点分布读取
- **WHEN** 教师查询某班级指定知识点的障碍分布
- **THEN** 返回该知识点内各 barrier_type 的作答统计

### Requirement: 教师覆盖与操作日志
系统 SHALL 提供 `PUT /api/diagnosis/override/{student_id}` 端点：教师 SHALL 指定目标学生、障碍类型（concept/reading/expression）、三维权重（默认 90/5/5）与覆盖原因；系统 SHALL 将覆盖结果写入 Student.barrier_profile、更新 barrier_last_updated、将学生画像标记为冻结，并 MUST 记录 BarrierOverrideLog（含操作教师 teacher_id、目标学生 student_id、覆盖前后值、原因、时间戳）；冻结画像 SHALL 使后续聚合跳过该学生（不被批量诊断结果覆盖），直到教师显式清除冻结；SHALL 提供只读端点读取该学生的覆盖历史。

#### Scenario: 覆盖写入画像
- **WHEN** 教师提交对某学生的障碍覆盖
- **THEN** barrier_profile 更新为新权重、barrier_last_updated 刷新且画像被标记冻结

#### Scenario: 覆盖操作留痕
- **WHEN** 教师执行覆盖
- **THEN** BarrierOverrideLog 新增一条含操作者、原因、时间戳的记录

#### Scenario: 冻结画像不被聚合覆盖
- **WHEN** 学生对同一考试再次 run-llm 且聚合触发
- **THEN** 该学生因画像冻结被聚合跳过，barrier_profile 保持教师覆盖值

### Requirement: 诊断配置管理
系统 SHALL 提供 `GET/PUT /api/diagnosis/config/{teacher_id}` 端点：教师 SHALL 可读取与更新其诊断阈值配置（BarrierConfig），默认值 SHALL 为连续错误阈值 3、连续正确阈值 2、低分阈值 3、预警阈值 3、启用标记 false；PUT SHALL 按 teacher_id upsert，仅更新请求中出现的字段，缺失字段保留默认或既有值。启用标记为 true 且存在连续错误 ≥ 连续错误阈值 的作答记录时，系统 SHALL 将对应诊断结果标记为「需关注」（diagnosis_flag=needs_attention）。

#### Scenario: 读取默认配置
- **WHEN** 教师首次读取自身诊断配置
- **THEN** 返回默认阈值（3/2/3/3/false）

#### Scenario: 部分字段 upsert
- **WHEN** 教师仅提交启用标记为 true
- **THEN** 其余阈值字段保持默认或既有值，不重置

### Requirement: 诊断权限控制
系统 SHALL 将 `diagnosis` 资源加入权限矩阵 RESOURCES：teacher+ 角色 SHALL 拥有 create/read/update 权限；student 角色 SHALL 仅拥有读取自身 history 的权限；越权访问 SHALL 返回 403。

#### Scenario: 教师可读写诊断数据
- **WHEN** teacher 角色调用 run-llm/override/config/barrier 端点
- **THEN** 请求被允许并正常执行

#### Scenario: 学生仅读自身历史
- **WHEN** student 角色访问自己 student_id 的 history 端点
- **THEN** 返回其诊断历史；访问他人 history 或统计端点则返回 403

### Requirement: 诊断数据模型与迁移
系统 SHALL 提供数据模型：`BarrierConfig`（教师诊断阈值配置，含连续错误/正确阈值、低分阈值、预警阈值、启用标记）、`BarrierOverrideLog`（覆盖操作日志，含 teacher_id/student_id/前后值/原因/时间戳）；`Student` SHALL 增加 `barrier_last_updated` 字段记录画像最后更新时间；`StudentAnswer` SHALL 增加平铺诊断列 `fused_conf`、`rule_conf`、`llm_conf`、`diagnosis_flag`（normal/manual_review/needs_attention/error）、`diagnosis_version`、`diagnosis_source`（rule/llm/fused/error）与 `diagnosis_detail` JSON（含 reasoning/suggestion/补救建议），既有 `barrier_type` 列保留为落库主判；系统 SHALL 提供 Alembic 迁移创建两张新表与新增多列，迁移 SHALL 可重复执行、可回滚，且 SHALL 对既有 barrier_profile 为 NULL/缺键/畸形 JSON 的数据做防御性归一化。

#### Scenario: 新模型可建表
- **WHEN** 执行迁移
- **THEN** barrier_config 与 barrier_override_log 表创建成功，student 表含 barrier_last_updated 列，student_answer 表含平铺诊断列

#### Scenario: 既有画像畸形数据防御归一化
- **WHEN** 迁移遇到 barrier_profile 为 NULL/缺键/非法 JSON 的既有学生
- **THEN** 迁移后读取画像 SHALL 恒返回 concept/reading/expression 三键且数值合法，不抛异常
