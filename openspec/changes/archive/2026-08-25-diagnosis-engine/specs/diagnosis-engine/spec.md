## Purpose

双引擎诊断核心：化学迷思概念规则引擎 + LLM 深度诊断 + 置信度融合，对单道错误作答判定三维障碍类型（concept/reading/expression），输出结构化诊断结论供上层服务消费。

## ADDED Requirements

### Requirement: 规则引擎基于 YAML 规则库判定障碍类型
系统 SHALL 提供基于 YAML 规则库的规则引擎：规则库 SHALL 包含 22 条化学迷思概念规则，组织于 6 大知识板块（化学平衡/氧化还原/摩尔计算/有机化学/化学用语/物构知识），每条规则 MUST 携带 `barrier_type`（concept/expression）、keywords、anti_keywords、patterns（正则）、severity、confidence_modifier、remediation_points、related_questions。引擎 SHALL 将题目正文与学生答案拼接为搜索文本，按关键词计数（大小写不敏感）与正则模式匹配；规则触发 MUST 满足关键词命中数 ≥ min_keyword_match（默认 2）且模式命中数 ≥ min_pattern_match（默认 1）；anti_keywords 任一命中 SHALL 排除该规则；置信度 SHALL 按 `confidence_base(0.85) + severity 加成 + keyword_rate×0.04 + pattern_rate×0.06 + confidence_modifier` 计算并限制在 [0,1]；输出 SHALL 包含按置信度降序排列的 matched_rules 与 top_diagnosis{barrier_type, category, name, confidence}。

#### Scenario: 勒夏特列方向混淆命中概念规则
- **WHEN** 学生作答体现"升温有利于放热反应"的勒夏特列方向混淆
- **THEN** 规则引擎命中 RULE_001（barrier_type=concept，category=化学平衡），且 top_diagnosis 置信度 > 0.7

#### Scenario: anti_keywords 排除规则
- **WHEN** 搜索文本命中某规则的 anti_keywords（如"理解错题意"）
- **THEN** 该规则被排除，不参与匹配结果

#### Scenario: 空答案无匹配
- **WHEN** 学生答案为空字符串
- **THEN** 规则引擎返回 matched_rules 为空列表

#### Scenario: 正确答案不触发规则
- **WHEN** 搜索文本与标准答案一致
- **THEN** 规则引擎不命中任何规则

#### Scenario: 多规则命中按置信度降序
- **WHEN** 一次诊断命中多条规则
- **THEN** matched_rules 按置信度降序排列

### Requirement: LLM 深度诊断输出结构化障碍结论
系统 SHALL 提供 LLM 深度诊断：MUST 以资深化学教师角色设置系统提示（含"不归因于粗心"原则与严格 JSON 输出约束），输入 MUST 包含题目正文（截断至 500 字）、学生答案、正确答案、历史错题（最多 5 条），可选包含 grade/avg_score/mastery_level（缺失时以"未知"填充）；MUST 内置 3 组 Few-shot 示例（勒夏特列/氧化还原/摩尔单位，覆盖不同知识板块）；输出 SHALL 为 JSON 对象，MUST 包含核心四字段 `barrier_type`（枚举 concept/reading/expression）、`confidence`（0.0-1.0）、`reasoning`、`suggestion`，可包含 `detail`（primary_misconception/category/root_cause/remediation/recommended_practice）。调用 SHALL 使用 temperature=0.3、max_tokens=2000、response_format=json_object（按 provider 能力切换：不支持 json_object 的 provider 以自由文本+JSON 约束提示）；解析 SHALL 支持从 markdown 代码块提取 JSON，MUST 校验核心四字段齐全（barrier_type/confidence/reasoning/suggestion，缺失任一判无效）、barrier_type 枚举合法性、confidence 数值范围；非法 JSON、缺字段或非法枚举均判为无效响应并重试最多 3 次，重试请求 SHALL 携带先前解析错误的纠错提示；重试耗尽 MUST 降级返回错误信号而非抛异常。

#### Scenario: 成功解析返回核心字段
- **WHEN** LLM 返回合法 JSON 且 barrier_type 为合法枚举值
- **THEN** 客户端返回含 barrier_type/confidence/reasoning/suggestion 的完整诊断结果

#### Scenario: 非 JSON 响应触发重试
- **WHEN** LLM 前两次返回非 JSON、第三次返回合法 JSON
- **THEN** 客户端重试后成功返回诊断结果

#### Scenario: 重试耗尽降级
- **WHEN** LLM 三次均返回不可解析响应
- **THEN** 客户端返回错误信号（含 source=llm、error 说明），不抛出未捕获异常

#### Scenario: 非法障碍类型被拒绝
- **WHEN** LLM 返回的 barrier_type 不是 concept/reading/expression 之一
- **THEN** 该结果被判为无效，按解析失败处理

#### Scenario: 缺失核心字段被判无效
- **WHEN** LLM 返回合法 JSON 但缺少 reasoning 字段
- **THEN** 该响应被判为无效并触发重试，直至三次耗尽降级为错误信号

### Requirement: 置信度融合与冲突消解
系统 SHALL 提供置信度融合引擎：MUST 将规则引擎 top_diagnosis 置信度与 LLM 置信度加权融合为 `fused_conf = clamp(0.6×rule_conf + 0.4×llm_conf, 0, 1)`（规则权重 0.6、LLM 权重 0.4，权重非归一化时自动归一化）；冲突检测 SHALL 在两侧 barrier_type 不一致时置 has_conflict=true；冲突消解 SHALL 遵循决策表——`rule_conf≥0.85` 以规则引擎为准、`llm_conf≥0.9` 以 LLM 为准、两侧均低于阈值（双低）或两侧均高但类别冲突（双高冲突）均标记"需人工审核"（落库取置信度较高侧）；仅单路有结果 SHALL 按该路权重折算（`0.6×rule_conf` 或 `0.4×llm_conf`），但置信度标签 SHALL 按源置信度判定而非折算后的 fused_conf；融合输出 SHALL 携带 `diagnosis_flag`（normal/manual_review/error），冲突或降级时置 manual_review/error；置信度标签 SHALL 为 `<0.6 low`、`0.6-0.8 medium`、`≥0.8 high`；补救建议合并 SHALL 按 question_id 去重。

#### Scenario: 两路一致高置信融合
- **WHEN** 规则与 LLM 均判定 barrier_type=concept 且置信度高
- **THEN** has_conflict=false，fused_conf 按 `0.6×rule+0.4×llm` 计算，标签为 high

#### Scenario: 类别冲突且规则高置信以规则为准
- **WHEN** 两侧 barrier_type 不一致且 rule_conf≥0.85
- **THEN** 以规则引擎的 barrier_type 为准

#### Scenario: 双低冲突标记需人工审核
- **WHEN** 两侧 barrier_type 不一致且 rule_conf<0.85 且 llm_conf<0.9
- **THEN** 结果标记 diagnosis_flag=manual_review，落库取置信度较高侧的 barrier_type

#### Scenario: 双高冲突判人工审核
- **WHEN** 两侧 barrier_type 不一致且两侧置信度均高（rule_conf≥0.85 且 llm_conf≥0.9）
- **THEN** 结果标记 diagnosis_flag=manual_review；教师覆盖修正后该标志被清除

#### Scenario: 仅规则引擎结果权重折算
- **WHEN** 仅规则引擎有结果、LLM 无结果
- **THEN** fused_conf = 0.6 × rule_conf

#### Scenario: 单侧结果按源置信度打标
- **WHEN** 仅规则引擎有结果且 rule_conf=0.9（fused_conf 折算后为 0.54）
- **THEN** 置信度标签按源置信度判定为 high（fused_conf 仍按 0.54 落库）

#### Scenario: 置信度标签边界
- **WHEN** fused_conf 分别为 0.0/0.599/0.6/0.799/0.8/1.0
- **THEN** 标签依次为 low/low/medium/medium/high/high

#### Scenario: 补救建议去重
- **WHEN** 规则引擎与 LLM 返回相同推荐题目 ID
- **THEN** 合并后的 recommended_practice 中该题目只出现一次
