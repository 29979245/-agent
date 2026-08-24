## Why

后端仅有数据层前瞻字段（`Question.audit_status` / `audit_report` JSON、`AuditStatus` 枚举），四维审核引擎、题目级审核、状态机与审核 API 全部未实现。AI 出题链路（`feat/question-workbench`）产出的题目目前没有任何安全/质量闸门，而 doc 26 将"系数配平 100% 准确率"定为 HARD RED LINE——审核引擎是出题闭环的前置依赖，必须先落地。

## What Changes

- **新增方程式级四维安全审核引擎**（系数配平 / 反应条件 / 产物正确性 / 分子结构）：纯确定性算法，配平准确率 100% 红线。
- **新增题目级 Four-Dimension Review**（科学性 / 难度匹配 / 知识点覆盖 / 区分度）：LLM 打分，每维 0-100，综合分加权和（0.4/0.25/0.2/0.15）。
- **双层合成规则**：综合分 ≥80 passed / 70-79 warning / <70 blocked；科学性单维 <70 直接 blocked。
- **审核状态机**：passed → 教师 approve 入库；warning → approve 放行（带复核标记）或打回重生成；blocked → 自动重生成 ≤3 次，3 次仍失败标记"出题失败"（不自动替换）。
- **审核 API**：新增 `POST /api/question/audit`、`POST /api/question/{id}/approve`、`POST /api/question/{id}/regenerate`；`POST /api/question/generate` 响应内嵌两层审核报告。
- **双层 `audit_report` JSON 结构**：方程式级 4 项 + 题目级 4 维评分 + composite + evidence，写入现有 `Question.audit_report` 字段。
- **集成范围**：AI 生成走两层审核；手动录入/OCR 导入只过方程式级硬闸；`balance_equation` 工具只跑方程式级；学生对话/实验模拟不纳入本次。

## Capabilities

### New Capabilities
- `audit`: 四维审核引擎与审核工作流——方程式级确定性四维校验 + 题目级 LLM 质量审核 + 双层合成 + 审核状态机 + 审核 API。

### Modified Capabilities

（无。`data-models` 已有 `Question.audit_report`/`AuditStatus` 字段，本次不改变数据模型需求。）

## Impact

- **代码**：`app/services/audit/`（空占位 → 实现四维校验）、新增题目级 review 服务、`app/api/v1/` 新增 audit router、`app/services/question/` 关联审核调用。
- **数据**：复用 `Question.audit_report`（JSON）字段，无需迁移；`AuditStatus` 枚举沿用。
- **API**：新增 3 个端点；`generate` 响应结构扩展（含双层报告）。
- **依赖**：题目级审核需要 LLM 服务调用层；方程式级依赖 RDKit + 自研配平算法（后端规范已定）。
- **测试**：86 道确定性测试（方程式级）+ 题目级标注评测 + L2 集成测试。
