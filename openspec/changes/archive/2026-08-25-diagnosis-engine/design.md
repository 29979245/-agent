## Context

现有基础（见 proposal.md - Why）：`StudentAnswer.barrier_type`（exam.py）、`Student.barrier_profile` JSON（org.py）、`BarrierType` 枚举（enums.py）均已存在，本阶段补齐写入路径、画像聚合与诊断服务闭环。缺失：`barrier_last_updated` 列、`BarrierConfig`/`BarrierOverrideLog` 模型、`diagnosis` 权限资源、规则引擎与 LLM 客户端。

可复用的仓库既有模式：
- **LLM 客户端**：`app/services/audit/review.py` 的 `ReviewLLMClient` Protocol + `FallbackReviewLLMClient`（三级 Fallback：MiMo→qwen→DeepSeek，每级 3 次重试 + 指数退避；`_chat` 当前未配 `llm_api_key` 即报错，真实 HTTP 传输待 LLM 基建）。诊断 LLM 客户端沿用同一模式，测试注入 mock。
- **权限**：`app/core/permissions.py` 的 `RESOURCES` + `ROLE_PERMISSIONS` 矩阵（资源级），`require_permission(resource, action)` 装饰器。
- **API 组织**：`app/api/v1/<domain>.py` 路由模块 + `main.py` 挂 `prefix=/api/<domain>`。
- **配置**：复用 `settings.llm_provider` / `settings.llm_api_key`。

## Goals / Non-Goals

**Goals:**
- 双引擎（规则 + LLM）在 **barrier_type（concept/reading/expression）** 轴上融合，输出结构化诊断（top_diagnosis、fused_conf、置信度标签、补救建议）。
- 诊断结果持久化：`StudentAnswer.barrier_type` 落库 → `Student.barrier_profile` 五步聚合 → `barrier_last_updated` 时间戳。
- 服务闭环 API：run-llm 批量诊断、班级分布、教师覆盖、配置 upsert、统计/知识点/历史读取。
- 三个引擎组件独立可测：纯函数逻辑 + 可注入 LLM 客户端，与 DB 解耦（除聚合需查询作答）。

**Non-Goals:**
- 前端诊断页面（独立前端 change）。
- 学习计划子系统（doc 27 §13）与 Agent 诊断工具（doc 57，未来阶段）。
- 规则引擎独立预分类、置信度持久化、实时（逐题）诊断。
- LLM 真实 HTTP 传输基建（沿用 review 模式，未配置 `llm_api_key` 即降级，测试用 mock）。

## Decisions

### D1. 诊断主轴：barrier_type 三维（非知识板块迷思概念）
融合、冲突检测、落库、画像、统计全部按 `barrier_type` 聚合；知识板块与迷思概念名仅作为补救建议附加信息。22 条规则按板块组织但每条额外携带 `barrier_type` 字段（化学平衡/氧化还原/摩尔/有机/物构→concept，化学用语→expression）。**reading 无规则覆盖**，由 LLM 单独诊断，融合走"仅 LLM"行。详见 `docs/adr/0001-diagnosis-axis-barrier-type.md`。

### D2. 组件划分与常量单一真值源
```
app/services/diagnosis/
  rule_engine.py      # ChemistryRuleEngine：YAML 加载 + 匹配 + 置信度
  llm_diagnosis.py    # LLMDiagnosisClient：Prompt/Few-shot/解析/重试降级
  fusion.py           # ConfidenceFusionEngine：加权融合 + 冲突消解决策表
  aggregation.py      # 画像聚合（纯函数 aggregate + 落库写入）
  rules/rules.yaml    # 22 条规则（单文件，随代码资产部署）
```
融合公式、决策表阈值、置信度标签、规则引擎超参与权重均以**模块级常量**表达，作为测试与 evals 断言的单一真值来源。备选：按知识板块拆 6 个 YAML 文件——单文件加载/测试更简单，22 条体量足够，选单文件。

### D3. 规则引擎
- **加载**：`yaml.safe_load` lazy 加载 + 模块级缓存；加载即做 schema 校验（必填字段/枚举/正则可编译），非法规则直接报错（fail-fast）。
- **匹配**：搜索文本 = 题目正文 + 学生答案，二者可分别赋权（证据权重：学生答案为主、题目正文为辅，评审 C13 折入）；关键词计数大小写不敏感；任一 `anti_keywords` 命中即排除该规则；触发条件 `keyword_hits ≥ MIN_KEYWORD_MATCH(2)` 且 `pattern_hits ≥ MIN_PATTERN_MATCH(1)`；空答案/与标准答案一致 → 无匹配。
- **置信度**：`conf = clamp(CONFIDENCE_BASE(0.85) + severity_bonus + keyword_rate×0.04 + pattern_rate×0.06 + confidence_modifier, 0, 1)`。其中 `keyword_rate`/`pattern_rate` 为命中数占该规则对应关键词/正则总数比例。`severity_bonus` 为设计决策：`(severity - 3) × 0.05`（severity=3 中性 → 0，5 → +0.10，1 → -0.10），属可调参数不改变 spec 满足性。
- **精度优先刻意偏差（评审 D10/T4）**：`CONFIDENCE_BASE=0.85` 与融合规则优先阈值 `0.85` 同值，意味着高置信规则几乎必然走「规则优先」行——这是**刻意设计**：规则引擎精确率 ~90% 优先于 LLM 召回率 ~85%，融合权重在规则高置信时从属于规则判定。已记录为意图而非缺陷；若后续需让融合真正参与高置信场景，将 `rule_conf≥0.85` 提至 `0.9` 并同步调 `CONFIDENCE_BASE`。
- **输出**：`matched_rules` 按置信度降序 + `top_diagnosis{barrier_type, category, name, confidence}`。

### D4. LLM 诊断客户端（沿用 review 模式）
- 新增 `DiagnosisLLMClient` Protocol（`complete(messages) -> str`）+ `FallbackDiagnosisLLMClient`（三级 Fallback），复用 `PROVIDER_CHAIN`/退避语义；生产无 `llm_api_key` 时 `complete` 抛错，由客户端包装为错误信号。
- **Prompt**：system = 资深化学教师角色 + "不归因于粗心"原则 + 严格 JSON 约束；内置 3 组 Few-shot（勒夏特列/氧化还原/摩尔单位，覆盖 concept/reading/expression 与不同板块）；user = 题目正文（≤500 字）/学生答案/正确答案/历史错题（≤5 条）＋可选 grade/avg_score/mastery_level（缺失填"未知"）。
- **解析/降级（四重硬化，评审 D5/F4）**：① 核心四字段齐全校验（barrier_type/confidence/reasoning/suggestion，缺失任一判无效）；② 解析顺序固定——先围栏提取 → 再整串 JSON 解析 → 失败后纠错重试；③ `response_format` 按 provider 能力切换（支持 json_object 用约束输出，不支持则自由文本 + JSON 约束提示）；④ 纠错重试 ≤3 次，重试 prompt 携带先前解析错误；`barrier_type` 必须 ∈ {concept,reading,expression}；耗尽返回错误信号对象（含 source=llm + error 说明），**不向上抛未捕获异常**。

### D5. 融合引擎
- `fused_conf = clamp(0.6×rule_conf + 0.4×llm_conf, 0, 1)`（权重非归一化时自动归一化）。
- 冲突：两侧 barrier_type 不一致 → `has_conflict=true`；消解决策表——`rule_conf≥0.85` 以规则为准、`llm_conf≥0.9` 以 LLM 为准、**双低与双高冲突**均标记"需人工审核"（落库取高侧）。双高冲突场景（评审 D4/F3）：两侧置信度均高但类别不一致 → `diagnosis_flag=manual_review`，教师 override 后清除。
- 仅单侧：`0.6×rule_conf` 或 `0.4×llm_conf`（fused_conf 仍按折算值落库）。
- 单侧打标（评审 D2/F1）：置信度标签**按源置信度判定**而非折算后的 fused_conf——仅规则 0.9 → 标签 high（fused 0.54）；标签与 fused_conf 解耦，避免单侧结果被折扣误判为 low。
- 输出携带 `diagnosis_flag`：normal / manual_review / needs_attention（config 接线，见 D9）/ error（LLM 降级）。
- 标签：`<0.6 low / 0.6-0.8 medium / ≥0.8 high`。
- 补救建议：规则 `related_questions` 与 LLM `recommended_practice` 按 question_id 去重合并。

### D6. 并发与事务边界
`run-llm` 批量：`ThreadPoolExecutor(max_workers=5)`，**子线程仅执行 `client.complete`（纯 LLM IO，不触碰 SQLAlchemy session/DB）**；主线程统一 parse → 校验 → 融合 → 单事务批量落库 → 触发画像聚合。备选：asyncio.gather —— 但 LLM 客户端当前为同步协议（同 review），保持同步线程池最小改动。

### D7. 画像聚合（五步）
纯函数 `aggregate(answers) -> {concept, reading, expression}`：查询已诊断错误 → 按 barrier_type 计数 → 归一化（分母 = 总数，为 0 时以 1 计，保留 2 位）→ 补零保证三键齐全 → 写 `Student.barrier_profile` + 更新 `barrier_last_updated`。触发点：run-llm 批量落库后按 student_id 分组聚合。**冻结语义（评审 D7/T1）**：聚合按 student 分组时跳过画像被冻结（教师 override 后置冻结标记）的学生，直到显式解除冻结——避免批量诊断聚合改写教师手动修正。

### D8. 数据模型与迁移
- `BarrierConfig`：teacher_id（唯一 FK）＋连续错误阈值（默认 3）/连续正确阈值（默认 2）/低分阈值（默认 3）/预警阈值（默认 3）/启用标记（默认 false）。**接线（评审 D9/T3）**：启用为 true 且连续错误 ≥ 阈值时，对应作答 `diagnosis_flag=needs_attention`。
- `BarrierOverrideLog`：teacher_id、student_id、before/after、reason、created_at。
- `Student` 新增 `barrier_last_updated`（DateTime，可空）。
- `StudentAnswer` 新增平铺诊断列（评审 D8/T2，吸收版本化 C8）：`fused_conf`/`rule_conf`/`llm_conf`（Float 可空）、`diagnosis_flag`（String：normal/manual_review/needs_attention/error）、`diagnosis_version`（String）、`diagnosis_source`（rule/llm/fused/error）、`diagnosis_detail`（JSON：reasoning/suggestion/recommended_practice）。既有 `barrier_type` 保留为落库主判。平铺列支持溯源与未来版本比对，`diagnosis_detail` 承载非结构化信息。
- 单个 Alembic 迁移：upgrade 建两表 + 加列，downgrade 回滚。**防御性归一化（评审 C33/C34）**：迁移与聚合读路径均须容忍既有 `barrier_profile` 为 NULL/缺键/畸形 JSON，返回补零三键不抛异常；downgrade 说明中标注 drop 平铺列将丢失诊断明细数据（增量非破坏，但回滚有数据丢失语义需记录）。

### D9. 权限
- `RESOURCES` 追加 `"diagnosis"`；`ROLE_PERMISSIONS`：admin/dept_admin 全权限、subject_lead 只读、teacher {create,read,update}、student {read}。
- 注意：矩阵是**资源级**，student 读取"自己的 history"需在端点内二次校验 `request.state.user.user_id == student_id`（与现有 student 资源处理方式一致）。

### D10. API 组织
`app/api/v1/diagnosis.py` 导出 `diagnosis_router`，`main.py` 挂 `prefix="/api/diagnosis"`。端点：`POST run-llm`、`GET barrier/{class_id}/{exam_id}`、`PUT override/{student_id}`、`GET override/{student_id}`（覆盖历史）、`GET/PUT config/{teacher_id}`、`GET class/{id}/stats`、`GET class/{id}/kp/{kp}`、`GET history/{student_id}`。请求参数经 Pydantic 校验（422 统一错误体）。

### D11. 评估（L3 eval 骨架，评审 D6/G5）
本 change 内先搭 eval 骨架：Golden 数据（勒夏特列/氧化还原/摩尔等真实错题样例带标注 barrier_type）+ runner，mock LLM 下做**结构断言**（输出字段齐全、枚举合法、标签符合阈值），准确率指标 ≥70% 以结构完整性为代理；真实 LLM 传输接入后切换为真实调用评估（target ≥70% 准确率）。骨架保证 prompt 变更可回归验证，不因 LLM 基建未就绪而阻塞。

## Risks / Trade-offs

- **LLM 不可用（未配 `llm_api_key`）** → run-llm 返回错误信号，规则引擎路径仍可用，融合走"仅规则"行（0.6×rule）。Mitigation：测试全部注入 mock 客户端；生产降级不 500。
- **reading 障碍无规则覆盖** → 融合恒走"仅 LLM"行，reading 判定依赖 LLM 质量。Mitigation：ADR 已记录，未来补 reading 类规则（doc 27 §十四）。
- **ThreadPoolExecutor + SQLAlchemy session 线程安全** → 子线程绝不触碰 session，主线程单事务提交。Mitigation：代码评审 + 并发测试（≤10 条批量）。
- **YAML 规则与代码字段漂移** → 加载即 schema 校验（fail-fast）+ 规则引擎单测断言恰好 22 条。Mitigation：测试覆盖字段完整性。
- **并发 run-llm 触发画像竞态** → 聚合按 student 分组，最后一次提交覆盖；SQLite WAL + 主线程串行写。可接受。

## Deferred (codex 策略 TODO，本 change 范围外)

评审外部声音（codex）提出的 5 项策略性强化，记为 TODO 不在本 change 实现：run-llm 幂等（并发重入，C9）；班级作用域 RBAC（class_id 授权细化，C22）；LLM 隐私与数据最小化（C29）；限流与背压（C30）；诊断可观测性指标（C36）。

## Migration Plan

1. **部署顺序**：先跑 Alembic 迁移（建 `barrier_config`/`barrier_override_log` 表 + `student.barrier_last_updated` 列），再启动新代码；规则 YAML 随包部署到 `app/services/diagnosis/rules/`。
2. **回滚**：`alembic downgrade`（drop 两表 + 列）＋ 回退代码（移除 `diagnosis` 资源与路由）。迁移为增量且非破坏性（新表新列，不动既有数据）。
3. **验证**：L1 规则/融合/聚合单测 ≥95%；L2 API 集成（run-llm mock、barrier 分布、override、config、权限 403）≥90%；勒夏特列端到端（A 级）验证双引擎融合全链路。

## Open Questions

无阻断性问题。`severity_bonus` 精确数值（D3）为设计取值 `(severity-3)×0.05`，属可调参数，调整不影响 spec 满足性，可在 evals 阶段按 Golden 数据微调。
