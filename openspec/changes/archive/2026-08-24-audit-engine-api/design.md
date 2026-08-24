## Context

数据层已就绪：`Question` 已含 `audit_status`（`AuditStatus` 枚举：passed/warning/blocked）与 `audit_report`（JSON 字段）；`app/services/audit/` 是空占位。见 proposal.md - Why 了解动机。

两层审核模型为本设计的核心前提（领域术语见 `chemai-backend/CONTEXT.md` 与课程资料）：
- **方程式级四维安全审核**（doc 26）：系数配平 / 反应条件 / 产物正确性 / 分子结构，确定性算法，配平准确率 100% 为 HARD RED LINE，不靠 LLM 配平。
- **题目级 Four-Dimension Review**（课程版）：科学性 / 难度匹配 / 知识点覆盖 / 区分度，每维 0-100，综合分加权和（0.4/0.25/0.2/0.15）。

已对齐的验收指标：方程式级 86/86 确定性测试 100%、条件/产物召回率 ≥80%、题目级科学性准确率 ≥75%、两层合成 overall_status 判定正确率 ≥90%。

## Goals / Non-Goals

**Goals:**
- 两层审核引擎（方程式级确定性四维 + 题目级 LLM 四维）及双层合成规则。
- 审核状态机（approve / 打回重生成 / 自动重生成 ≤3 次）。
- 审核 API（generate 内嵌报告、re-audit、approve、regenerate）。
- 双层 `audit_report` JSON 结构写入现有字段，无需 DB 迁移。

**Non-Goals:**
- 学生对话 / 实验模拟的审核（本次不纳入）。
- 手动录入 / OCR 导入的题目级评审（跳过）。
- 配平工具跑题目级评审（只跑方程式级）。
- 失败题自动替换题库已有题目（教师决定）。
- `QuestionSet` / `knowledge_points` 结构等数据模型改动（超出本次范围，见 Open Questions）。

## Decisions

### D1. 方程式级引擎：确定性四维，拆独立纯函数模块
每个维度是独立纯函数，输入方程式字符串，输出 `pass/fail + 证据`：
- **系数配平**：自研确定性配平算法（矩阵秩/整数系数求解，符合后端规范"不靠 LLM 配平"），任何失败返回 `blocked`——宁拦不错。这是 100% 红线所在。
- **反应条件**：规则库 + 已知反应知识库匹配，目标召回率 ≥80%。漏报走 `warning` 而非 `blocked`（证据不足不阻断）。
- **产物正确性**：规则 + RDKit 校验产物化学式/原子守恒，目标召回率 ≥80%。
- **分子结构**：RDKit 分子有效性/价键合法性校验。

*替代方案*：LLM 配平 → 拒绝。非确定性、无法保证 100% 红线。*替代方案*：单文件大函数 → 拒绝，86 测试需要按维度独立定位。

### D2. 题目级评审：LLM 四维打分 + 固定权重合成
独立服务，输入题目全量字段（正文/选项/答案/解析/知识点/难度标签），走现有 LLM fallback 链（MiMo→qwen-turbo→DeepSeek，每级重试）。
- 每维 0-100 整数评分；`composite = 0.4×科学性 + 0.25×难度 + 0.2×知识点 + 0.15×区分度`。
- 判定：composite ≥80 → passed；70-79 → warning；<70 → blocked；**科学性单维 <70 → 硬阻断**（无论 composite）。
- 输出带 evidence（每维一句理由），供教师复核。

*替代方案*：规则打分代替 LLM → 拒绝，四维评审本质是语义质量判断，规则无法覆盖。*替代方案*：让 LLM 直接输出状态 → 拒绝，固定权重 + 单维红线更可解释、可评测。

### D3. 双层合成规则
`overall_status`：任一层 `blocked` → `blocked`；否则任一层 `warning` → `warning`；否则 `passed`。合成函数为纯函数，作为 86/90% 正确率评测的单一真值来源。

### D4. 双层 audit_report JSON 结构
沿用现有 `Question.audit_report` 字段（无迁移），结构：
```
{
  "equation_level": { 系数配平/反应条件/产物正确性/分子结构: {status, evidence} },
  "question_level": { 科学性/难度匹配/知识点覆盖/区分度: {score, evidence}, "composite": 84.25 },
  "overall_status": "passed|warning|blocked",
  "meta": { "regeneration_attempts": 0, "generation_failed": false }
}
```
**"出题失败"标记不新增枚举/迁移**：`AuditStatus` 保持 passed/warning/blocked，失败标记落在 `meta.generation_failed=true`。重生成计数落 `meta.regeneration_attempts`。

### D5. 审核状态机
- `passed` → 教师 approve → 入库。
- `warning` → 教师 approve（`meta.review_flag=true` 复核标记）放行入库，或打回重生成。
- `blocked` → 自动重生成 ≤3 次（每次重新走两层审核，`meta.regeneration_attempts` 递增）；3 次仍 `blocked` → 标记 `generation_failed=true`（生成/重生成响应报告"出题失败"，不自动替换）。

状态机为纯函数：`(overall_status, action) → next_state`，便于 L1 单元测试。

### D6. 审核 API
新增 `app/api/v1/audit.py` router，全部依赖现有 JWT + teacher+ 权限（复用 `core/` 依赖注入）：
- `POST /api/question/generate`（扩展现有）：响应内嵌双层审核报告。
- `POST /api/question/audit`：对已存储题目重新审核，返回双层报告。
- `POST /api/question/{id}/approve`：passed → 入库；warning → 入库带复核标记。
- `POST /api/question/{id}/regenerate`：重新生成 + 两层重审。

### D7. 集成点
- AI 生成管线（`services/question/`）：生成 → 方程式级 → 题目级 → 合成 → 状态机。
- 手动录入 / OCR 导入：只调方程式级硬闸。
- `balance_equation` 工具：只调方程式级。
- 学生对话 / 实验模拟：不接入。

## Risks / Trade-offs

- **配平算法对复杂氧化还原/离子方程式出现边缘失败** → 用 doc 26 测试矩阵（86 用例）覆盖；未识别情况 fail-safe 返回 blocked（宁拦不错），不会放行错误题目。
- **题目级 LLM 评分波动** → 70-79 warning 缓冲带 + 科学性 <70 硬拦兜底；评测基线对比（run_evals --compare）防劣化。
- **反应条件 / 产物正确性召回率可能 <80%** → 规则库迭代 + 评测基线对比；漏报降级 warning 而非阻断。
- **无迁移约束下无法新增"出题失败"枚举** → 落在 `meta.generation_failed` JSON 字段；前端/生成响应据此展示，不影响 AuditStatus 语义。
- **LLM 评分不可复现影响 90% 合成正确率评测** → 合成正确率评测用固定 mock LLM 分数输入，只测合成纯函数，不测 LLM 本身。

## Migration Plan

纯新增服务与端点，无 DB 迁移（复用 `audit_report` JSON）。部署：注册 `audit` router 即生效。回滚：不注册 router / 移除服务调用即回退，无数据风险。`generate` 响应结构向后兼容（内嵌报告为新增字段）。

## Open Questions

- `Question.knowledge_points` 为 `String(500)` 而非结构化列表，可能降低题目级评审的"知识点覆盖"输入质量 → 可延后，不影响本次 API 与状态机落地；作为后续数据模型改动独立处理。
