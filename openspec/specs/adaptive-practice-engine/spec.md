## Purpose

消费诊断输出的障碍画像与作答历史，计算学生 ZPD 难度档位、薄弱知识点与主导障碍，并依据策略矩阵通过题库抽样生成个性化练习题参数，供练习 API 与每日推送消费（服务层，设计文档 28）。

## Requirements

### Requirement: ZPD 难度计算

系统 SHALL 查询该学生最近 30 条作答记录（按 answered_at 降序取最新数据）计算正确率 = 答对数 / 总题数，并按三档映射返回难度：正确率低于 40% 返回 `easy`；在 40% 到 70% 之间（含两端）返回 `medium`；高于 70% 返回 `hard`。无历史作答记录（冷启动）SHALL 直接返回 `medium`。第 4 档 `competition` SHALL 不参与 ZPD 分配。

#### Scenario: 冷启动默认 medium

- **WHEN** 学生没有任何作答记录（新学生）
- **THEN** ZPD 难度返回 `medium`，不做任何正确率统计

#### Scenario: 边界值归属 medium

- **WHEN** 学生正确率恰好为 40% 或 70%
- **THEN** ZPD 难度返回 `medium`（40% 与 70% 两个临界值均归 medium）

#### Scenario: 低于 40% 返回 easy

- **WHEN** 学生正确率低于 40%
- **THEN** ZPD 难度返回 `easy`

#### Scenario: 高于 70% 返回 hard

- **WHEN** 学生正确率高于 70%
- **THEN** ZPD 难度返回 `hard`

### Requirement: 薄弱知识点提取

系统 SHALL 统计该学生的全部错误作答记录（不设时间窗口），遍历每条错误作答通过题目关联的 `knowledge_points` 列表逐点累加计数，返回错误频次最高的前 N 个知识点（默认 N=3）。一道题标记多个知识点时 SHALL 逐点计入计数，不丢失关联。可用知识点不足 3 个时 SHALL 由调用方传入的知识点参数补足。

#### Scenario: 一题多知识点计数

- **WHEN** 一道错题同时标记了"氧化还原反应"与"电子转移"两个知识点
- **THEN** 两个知识点各累加一次计数

#### Scenario: 无错题记录

- **WHEN** 学生没有错误作答记录
- **THEN** 返回空列表，由调用方传入的知识点作为出题目标

### Requirement: 主导障碍识别

系统 SHALL 读取学生记录的障碍画像字段（`Student.barrier_profile`），取占比分数最高的障碍类型键名作为主导障碍。字段不存在或格式异常时 SHALL 默认返回 `concept`（概念理解型）。

#### Scenario: 画像存在取占比最高

- **WHEN** 障碍画像为 `{"concept":0.7,"reading":0.15,"expression":0.15}`
- **THEN** 主导障碍返回 `concept`

#### Scenario: 画像缺失默认 concept

- **WHEN** 学生从未被诊断、障碍画像为空或格式异常
- **THEN** 主导障碍默认返回 `concept`

### Requirement: 题库抽样出题

系统 SHALL 依据目标知识点列表、难度档位与题型（初始版本仅 `choice` 选择题）从真题库检索并复制抽样题目作为练习题（`source=practice`，含题目内容/选项/答案/解析/知识点/难度），组装为个性化练习题目列表。抽样 SHALL 排除调用方指定排除的题目（如刚做错的原题）。LLM 出题服务接入前 SHALL 走确定性抽样，不依赖 LLM 可用性。

#### Scenario: 按知识点与难度抽样

- **WHEN** 目标知识点为"氧化还原反应"、难度为 `medium`
- **THEN** 返回该知识点下 `medium` 难度的题目副本，标记来源为日常练习

#### Scenario: 抽样不足或失败兜底

- **WHEN** 真题库中目标知识点题目不足或检索失败
- **THEN** 返回已抽样题目，并在结果中标记抽样不足，不阻断练习创建

### Requirement: 批次限制

系统 SHALL 对单次个性化出题的学生数量做硬限制，每批最多 5 名学生；超过 5 人时 SHALL 返回分批提示，由调用方分批触发。

#### Scenario: 超过 5 人分批提示

- **WHEN** 调用方一次传入 6 名及以上学生请求出题
- **THEN** 本批仅处理前 5 名并提示剩余学生数量
