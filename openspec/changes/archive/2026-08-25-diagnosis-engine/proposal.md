## Why

将诊断从"知道谁错了"进化到"知道为什么错"（doc 27）。本阶段落地完整障碍诊断闭环：规则引擎 + LLM 双引擎融合判定三维障碍类型（concept/reading/expression），并把判定结果写入学生作答与画像，供下游学情聚合（doc 50）、自适应练习（doc 49）与 Agent 诊断工具（doc 57）消费。

## What Changes

- 新增 `app/services/diagnosis/` 三个引擎组件：
  - **ChemistryRuleEngine**：22 条化学迷思概念 YAML 规则（6 大知识板块，每条携带 `barrier_type`），关键词计数 + 正则匹配 + anti_keywords 排除，输出 `top_diagnosis{barrier_type, category, name, confidence}`
  - **LLMDiagnosisClient**：Few-shot（3 示例）+ 四输入 prompt，输出核心四字段 `barrier_type/confidence/reasoning/suggestion` + `detail` 软字段；重试 3 次、JSON 预处理、异常降级
  - **ConfidenceFusionEngine**：`fused = clamp(0.6×rule + 0.4×llm)`，冲突消解决策表，置信度标签（low/medium/high）
- 数据模型：`Student.barrier_last_updated` 字段、`BarrierConfig`（教师阈值）、`BarrierOverrideLog`（覆盖操作日志）
- 新增 `app/api/v1/diagnosis.py`（`/api/diagnosis/*`）：`run-llm` 批量诊断、`barrier` 班级分布、`override` 教师覆盖、`config` 教师配置 upsert、`class/{id}/stats`、`class/{id}/kp/{kp}`、`history/{student_id}`
- 画像聚合：`StudentAnswer.barrier_type` → `Student.barrier_profile`（五步、补零保三键、更新时间戳）
- 权限：`diagnosis` 加入 `RESOURCES`（teacher+ 读写、student 只读自己的 history）
- 测试：规则引擎 8 + LLM mock 7 + 融合 7 + 勒夏特列端到端（A 级）+ API 集成

不在本次范围（推迟）：学习计划子系统（doc 27 §13，doc 57 Agent 阶段）、前端诊断页面（独立前端 change）、规则引擎独立预分类/置信度持久化/实时诊断（未来迭代）。

## Capabilities

### New Capabilities

- `diagnosis-engine`: 双引擎诊断核心——化学迷思概念规则引擎、LLM 深度诊断（Few-shot + JSON Schema）、置信度融合（加权 + 冲突消解决策表），可独立于数据库测试。
- `diagnosis-api`: 诊断服务闭环——批量诊断触发（run-llm）、画像聚合持久化、班级/知识点/历史统计读取、教师覆盖与配置、数据模型（BarrierConfig/BarrierOverrideLog/barrier_last_updated）。

### Modified Capabilities

<!-- 无：BarrierType/barrier_profile 等字段已由 data-models 既有规格覆盖，本阶段仅实现写入路径，不改既有需求。 -->

## Impact

- **代码**：`app/services/diagnosis/`（新）、`app/api/v1/diagnosis.py`（新）、`app/db/models/`（+2 模型、+1 字段）、`app/core/permissions.py`（+resource）、`app/main.py`（+router）、Alembic（+迁移）
- **API**：新增 `/api/diagnosis/*` 8 个端点
- **依赖**：PyYAML（YAML 规则解析）
- **数据库**：`barrier_config`、`barrier_override_log` 新表；`student.barrier_last_updated` 新列
- **配置**：`diagnosis` resource 权限矩阵；LLM provider 复用现有 `llm_provider/llm_api_key`
