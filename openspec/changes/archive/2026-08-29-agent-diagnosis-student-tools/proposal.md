## Why

诊断与学生 7 工具已在 `app/agents/tools/tools_diagnosis.py` 落地，但对照设计文档 28/30 存在 2 处契约缺口：

1. `assign_adaptive_practice` 直接写库（`generate_batch → generate_for_student` 创建 `ExamRecord` 并复制题目），违反 doc 28 §六——"Agent 版本用于教师预览和确认（**不写数据库**），确认后由前端调用 API 完成持久化"。
2. `diagnose_barrier` 班级诊断分支无家长数据访问门控（Guard 四层无 data-access 层，工具内 `_assert_parent_ok` 仅在个体路径），parent 角色可拉取全班学生画像，违反 doc 30 §4.2"家长仅可查看自己孩子的学情"。

探索阶段识别的其他项（`weak_knowledge_points` 增强、`generate_learning_plan` 三段式路由、班级 distribution 均值语义）经讨论判定为非缺口或低优先，本期不做。

## What Changes

- **`assign_adaptive_practice` 改为 preview-only**（doc 28 §六）：工具返回每生预览（ZPD 难度 / 难度档位 / 主导障碍 / 知识点 / 选中题目引用），**不创建 `ExamRecord`、不复制题目入库**。
- **新增 REST 确认落库端点**：接收教师确认后的批量预览结果，逐生创建 `ExamRecord` + 复制题目（复用引擎抽样逻辑），作为"确认后由前端调用 API 持久化"的落库路径。
- **审批门控从工具移至 API**：`TOOL_META` 移除 `assign_adaptive_practice` 的 `approval=True`、Guard `APPROVAL_TOOLS` 移除该工具；确认落库端点承载审批/权限门控。预览无副作用不再需要审批。
- **`diagnose_barrier` 班级诊断家长门控**（doc 30 §4.2）：parent 角色携带班级参数调用时返回 `ForbiddenError`（家长仅支持个体诊断）；班级统计分布仅 teacher 角色可获取。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `agent-core`: 工具切片——诊断组需求——`assign_adaptive_practice` 由"生成并布置、需审批"改为"预览不落库，审批移至 API 确认端点"；新增家长班级诊断隐私场景。
- `practice-api`: 新增需求"自适应练习确认落库"——提供 REST 端点接收确认后的批量预览并逐生持久化。

## Impact

- **代码**：`app/agents/tools/tools_diagnosis.py`（`assign_adaptive_practice` preview + `diagnose_barrier` 家长门控）、`app/agents/tools/tool_meta.py` 与 `app/agents/guard.py`（审批移除）、`app/services/exercise/adaptive.py`（新增 preview 模式）、`app/api/v1/practice.py`（新确认端点）。
- **API**：新增 `POST /api/practice/adaptive/confirm`。
- **测试**：`tests/unit/test_agent_tools_diagnosis.py`（preview 断言、家长班级拒绝、审批移除）、practice API 集成测试（确认落库端点）。
- **前端**：确认流程从"审批卡片执行工具直接布置"变为"工具返回预览 → 教师确认 → 前端调用 API 落库"。
- **无数据迁移**，无新增第三方依赖。
