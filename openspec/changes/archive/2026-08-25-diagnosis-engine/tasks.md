## 1. 数据模型与迁移

- [x] 1.1 在 `app/db/models/` 新增 `BarrierConfig`（teacher_id 唯一 FK + 连续错误 3/连续正确 2/低分 3/预警 3/启用 false）与 `BarrierOverrideLog`（teacher_id/student_id/before/after/reason/created_at）模型，`Student` 增加 `barrier_last_updated` 列；验证模型可建表、`pytest` 数据模型用例通过
- [x] 1.1a **[T2 折入]** `StudentAnswer` 增加平铺诊断列 `fused_conf`/`rule_conf`/`llm_conf`/`diagnosis_flag`/`diagnosis_version`/`diagnosis_source` + `diagnosis_detail` JSON（既有 `barrier_type` 保留为落库主判）；验证模型建表 + `pytest` 用例覆盖新增列
- [x] 1.2 生成 Alembic 迁移（建两表 + 加列）；验证 `alembic upgrade head` 成功且 `alembic downgrade -1` 可回滚
- [x] 1.2a **[T11 折入]** 迁移对既有 `barrier_profile` 为 NULL/缺键/畸形 JSON 做防御性归一化（读路径补零三键不抛异常）；`downgrade` 说明标注 drop 平铺诊断列的数据丢失语义；验证畸形数据迁移后读取正常

## 2. 规则引擎（ChemistryRuleEngine）

- [x] 2.1 编写 `app/services/diagnosis/rules/rules.yaml`（22 条，6 大知识板块，每条含 id/category/name/barrier_type/keywords/anti_keywords/patterns/severity/confidence_modifier/remediation_points/related_questions）；验证加载用例断言恰好 22 条且 schema 字段完整、正则可编译
- [x] 2.2 实现 `rule_engine.py`（lazy 加载 + schema 校验 + 搜索文本拼接 + 关键词计数/正则匹配/anti_keywords 排除 + 置信度公式 + top_diagnosis）；按 TDD 先写测试：勒夏特列方向混淆命中（置信度>0.7）、anti_keywords 排除、空答案无匹配、正确答案不触发、多规则按置信度降序、置信度 clamp、severity 加成、min 阈值不触发——验证规则引擎 8 个单测全部通过
- [x] 2.2a **[T9 折入]** 搜索文本证据加权：学生答案为主、题目正文为辅（评审 C13）；验证单测：仅题目正文命中 vs 答案命中时置信度差异符合权重
- [x] 2.2b **[T4 折入]** `design.md` D3 记录精度优先刻意偏差（CONFIDENCE_BASE=0.85 与规则优先阈值 0.85 同值属意图非缺陷）；验证文档段落存在

## 3. LLM 诊断客户端（LLMDiagnosisClient）

- [x] 3.1 实现 `llm_diagnosis.py` 的 Prompt 构建（资深化学教师 system + 不归因粗心 + JSON 约束；3 组 Few-shot：勒夏特列/氧化还原/摩尔单位；user 含题目≤500 字/学生答案/正确答案/历史≤5 条 + 可选 grade/avg_score/mastery_level 缺省"未知"）；验证 prompt 组装单测（字段齐全、截断生效）
- [x] 3.2 实现 `DiagnosisLLMClient` Protocol + `FallbackDiagnosisLLMClient`（三级 Fallback）与解析/重试/降级：markdown 围栏 JSON 提取、barrier_type 枚举校验、非法响应重试 ≤3、耗尽返回含 source=llm 的错误信号（不抛未捕获异常）；验证 LLM mock 7 个单测：成功解析核心四字段、非 JSON 第三次成功、重试耗尽降级、非法枚举拒绝、detail 可选字段、错误信号结构、输入字段拼装
- [x] 3.2a **[T7 折入]** JSON 解析四重硬化（评审 D5/F4）：① 核心四字段齐全校验（缺失任一判无效）② 固定解析顺序（围栏→整串→纠错重试）③ `response_format` 按 provider 能力切换 ④ 纠错重试 prompt 携带先前错误；验证 mock 单测：缺 reasoning 判无效触发重试、不支持 json_object 的 provider 走自由文本路径

## 4. 融合引擎（ConfidenceFusionEngine）

- [x] 4.1 实现 `fusion.py`：`fused = clamp(0.6×rule + 0.4×llm)`（权重非归一化自动归一化）、冲突检测 has_conflict、决策表（rule≥0.85 规则胜 / llm≥0.9 LLM 胜 / 双低"需人工审核"取高侧）、仅单侧权重折算、置信度标签（<0.6 low/0.6-0.8 medium/≥0.8 high）、补救建议按 question_id 去重；按 TDD 验证融合 7 个单测：双高一致融合、规则高置信冲突以规则为准、LLM 高置信冲突以 LLM 为准、双低标记人工审核、仅规则/仅 LLM 折算、标签边界（0.0/0.599/0.6/0.799/0.8/1.0）、补救去重
- [x] 4.1a **[T5 折入]** 单侧结果按源置信度打标（仅规则 0.9 → 标签 high，fused 仍 0.54 落库；标签与 fused_conf 解耦）；验证单测：单侧高置信标签正确
- [x] 4.1b **[T6 折入]** 双高冲突（两侧置信度均高且类别不一致）→ `diagnosis_flag=manual_review`，融合输出携带 diagnosis_flag（normal/manual_review/needs_attention/error）；验证单测：双高冲突落 manual_review、正常落 normal

## 5. 画像聚合（aggregation）

- [x] 5.1 实现纯函数 `aggregate(answers) -> {concept, reading, expression}`（计数 → 归一化分母 total or 1 → 保留 2 位 → 补零保三键）；验证单测：无错误作答不除零、仅 concept 补零 reading/expression、保留 2 位
- [x] 5.2 实现落库写入（按 student_id 分组更新 `Student.barrier_profile` + `barrier_last_updated`）；验证 DB 集成测试：聚合后画像与时间戳正确更新
- [x] 5.2a **[T1 折入]** 冻结语义：教师 override 后该学生画像被冻结，聚合按组跳过直到显式解除冻结；验证 DB 集成测试：覆盖后 run-llm 聚合不覆盖冻结画像、解除冻结后恢复聚合

## 6. 勒夏特列端到端（A 级）

- [x] 6.1 以勒夏特列方向混淆错误作答为输入，串联规则引擎 + LLM mock（判 concept）+ 融合引擎，输出 top_diagnosis/fused_conf/置信度标签/补救建议；验证 e2e 测试：barrier_type=concept、has_conflict=false、置信度标签正确、无重复补救题目

## 7. 权限（diagnosis resource）

- [x] 7.1 `RESOURCES` 追加 `diagnosis`，`ROLE_PERMISSIONS` 增加对应矩阵（admin/dept_admin 全权限、subject_lead 只读、teacher {create,read,update}、student {read}）；验证权限单测：teacher 可 run-llm/override/config、student 仅 read、越权返回 403

## 8. API 层与挂载

- [x] 8.1 实现 `app/api/v1/diagnosis.py` 的 `POST run-llm`（≤10 条、barrier_type IS NULL 优先、ThreadPoolExecutor(max_workers=5) 子线程仅 LLM IO、主线程单事务落库 + 触发聚合、失败不中断、无数据提示）；验证 API 集成测试：mock LLM 下批量诊断全链路、超限截断、单条失败其余成功、无待诊断提示
- [x] 8.1a **[T11 折入]** run-llm 失败状态持久化：单条失败落 `diagnosis_flag=error` + `diagnosis_detail` 含原因，下次 run-llm 对失败条目可重跑；验证集成测试：失败条持久化、重跑只处理失败条目
- [x] 8.2 实现只读端点 `GET barrier/{class_id}/{exam_id}`（当前考试无数据回退历史画像）、`GET class/{id}/stats`、`GET class/{id}/kp/{kp}`、`GET history/{student_id}`；验证集成测试：分布返回、回退行为、知识点统计、学生仅读自身 history（他人返回 403）
- [x] 8.2a **[T10 折入]** barrier 分布响应带 source（当前考试/历史回退）与 denominator（分母说明）标识；验证集成测试：分布字段含 source/denominator、回退路径 source 正确
- [x] 8.3 实现 `PUT override/{student_id}`（90/5/5 权重 + BarrierOverrideLog 留痕 + 刷新 barrier_last_updated）与 `GET override/{student_id}`（覆盖历史）、`GET/PUT config/{teacher_id}`（upsert，默认 3/2/3/3/false，部分字段更新不重置其余）；验证集成测试：覆盖写入与日志、配置默认值与部分更新
- [x] 8.3a **[T3 折入]** config 接线：启用为 true 且连续错误 ≥ 阈值时对应作答 `diagnosis_flag=needs_attention`；验证集成测试：阈值触发标记、停用后不再标记
- [x] 8.4 在 `main.py` 挂载 `diagnosis_router`（prefix=/api/diagnosis）；验证整体回归：`pytest tests/ --tb=short` 全绿 + 权限 403/认证 401/校验 422 错误体正确
- [x] 8.4a **[T12 折入]** kp 统计端点与 question 表关联校验 + 新增诊断权限回归断言（teacher 可读写、student 仅自身 history）+ rules.yaml 随包部署检查（`pip show`/打包清单含 YAML）；验证：关联 JOIN 正确、越权 403、YAML 在 wheel 内
- [x] 8.5 更新 `CONTEXT.md` 术语与架构小节并运行 `graphify update .` 同步知识图谱；验证 `graphify query` 可定位诊断服务

## 9. 验收

- [x] 9.1 全量验证：L1（规则 8 + LLM 7 + 融合 7 + 聚合 + 折入项 4.1a/4.1b）单测覆盖率 ≥95%、L2 API 集成 ≥90%、勒夏特列 e2e（A 级）通过；`pytest tests/ --tb=short -x` 全绿
- [x] 9.1a **[T8 折入]** L3 eval 骨架：Golden 数据（勒夏特列/氧化还原/摩尔样例带标注 barrier_type）+ runner，mock LLM 下结构断言（字段齐全/枚举合法/标签符合阈值）通过，准确率代理 ≥70%；验证：`pytest tests/eval/` 绿 + 结构断言覆盖
- [x] 9.1b **[T13 折入]** 端到端回归：权限 403/认证 401/校验 422 错误体正确 + override 冻结链路（覆盖→冻结→聚合跳过→解除）全通；验证：`pytest tests/ --tb=short` 全绿
