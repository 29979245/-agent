## Purpose

提供基于 ChromaDB 的真题/题库相似题检索：每知识点一向量索引、两段检索（关键词初筛+向量精筛）、相似度返回与自身排除，支撑出题变体与相似题推荐。

## ADDED Requirements

### Requirement: 向量索引构建
系统 SHALL 将题目按其每个知识点各生成一个独立向量（`::kp-N` 分片）写入 ChromaDB，支持 replace（清库重建）与 append（追加）模式；索引维度与当前 Embedding 模型不一致时自动清库重建。

#### Scenario: 全量重建索引
- **WHEN** 系统以 replace 模式构建索引
- **THEN** 集合被清空后按每知识点一向量写入全部题目

#### Scenario: 维度不匹配自动重建
- **WHEN** 集合已有向量维度与当前 Embedding 输出维度不一致
- **THEN** 系统清空集合后重新构建，避免维度异常

#### Scenario: 追加同步
- **WHEN** 新题目通过 import-questions 或保存入库后触发索引同步
- **THEN** 新题向量以 append 模式追加，不影响既有向量

### Requirement: 相似题检索
系统 SHALL 提供相似题检索：先关键词匹配缩小候选（Top-20），再对候选做向量精筛返回 Top-K，每条结果含相似度分数，且排除待检索题目自身。

#### Scenario: 两段检索返回相似题
- **WHEN** 教师对某题目发起相似题检索
- **THEN** 系统返回按相似度降序的 Top-K 题目，每条含 similarity

#### Scenario: 排除自身
- **WHEN** 检索的候选包含待检索题目本身
- **THEN** 结果中不包含该题目自身

#### Scenario: ChromaDB 不可用降级
- **WHEN** ChromaDB 不可用或检索异常
- **THEN** 系统降级为仅关键词匹配结果，不中断检索功能

### Requirement: 检索上下文用于变体（deferred · 本期不实现）
系统 SHOULD 将相似题检索结果（content 与 answer）作为 RAG 上下文注入 LLM 变体生成，标记来源（match_method=vector/simple/blueprint）与相似度。**本变更仅落地检索侧（相似题检索 Requirement）**；注入动作依赖出题管线（RAG 生成）后续阶段实现，故本期标 deferred。

#### Scenario: RAG 上下文注入
- **WHEN** 变体生成请求携带蓝本题或检索到 ≥3 道相似真题（后续阶段）
- **THEN** 相似题内容与答案以"基于以下真题生成变种题"格式注入 LLM 提示词
