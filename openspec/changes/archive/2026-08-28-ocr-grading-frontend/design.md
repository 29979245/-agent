## Context

后端 OCR 批改管线已完整落地（见 `openspec/specs/ocr-grading` 与归档 change `2026-08-27-ocr-grading-backend`）：上传会话状态机、双引擎识别 + APScheduler 5s 轮询、`/api/grading/*` 批改判卷、分档落库并触发诊断。前端无对应页面，仅 Agent 对话工具可触发批改。本 change 按原型 `stitch-prototypes/pages/ocr.html` 与设计文档 40 §1.6/§1.7 落地教师端 `frontend/pages/ocr.html` 全闭环页面，沿用既有页面约定（`panel.html`/`exam-v2.html`：自托管字体、设计令牌、IIFE + `escapeHtml`、`ChemAuth` 守卫）。

## Goals / Non-Goals

**Goals:**
- 单页完成"上传 → 进度 → 自动批改 → 结果审核 → 保存"闭环，教师无需切到 Agent 对话。
- 复用全部既有后端端点，后端改动收敛为批改结果 items 注入 `has_options`。
- 结果表格还原原型列（姓名/学号/总分/选择题/填空题/状态/操作）。

**Non-Goals:**
- 不实现 `diagnosis.html`（"开始诊断障碍"仅占位链接，独立 change）。
- 不改 UploadSession 状态机、Agent 工具、诊断管线；不做多校/跨班聚合。
- 不做前端题库导入线（原型 9.1 的识别+手动修正表属另一页面）。

## Decisions

**1. 全闭环自动批改（vs 纯展示）**
标题栏放班级→考试联动下拉；上传后前端 5s 轮询 `GET /api/ocr/tasks/batch/{id}`，当 `total == done` 且批次非终态时，自动调 `POST /api/grading/run`（携带 exam_id，模式1）。批改中再次轮询 `GET /api/grading/results/{id}` 渲染表格。
- 备选（弃）：严格按文档 §1.7 由 Agent 触发 → 与原型"上传即出结果"观感不符，且用户已定全闭环。

**2. `has_options` 注入点：`run_grading` 内、持久化到 `task.result`**
`batch.run_grading` 已有 `q_by_pos`（位置→Question）。在 `grade_submission` 后遍历 items，命中 Question 时补 `has_options=bool(q.options)`，写入 `result["grading"]` 随 `task.result` 落库，`results_response` 自然带出，无需二次查询。
- 备选（弃）：改 `grade_submission` 签名传入 options → 耦合纯批改函数，且无考试路径（模式2/3）无处取 Question。

**3. multipart 上传封装在 `api.js`**
现有 `request()` 只发 JSON。新增 `uploadOcrBatch(files)`：`FormData` + Bearer，不设 `Content-Type`，复用 `handleAuth`/`extractDetail` 错误形状（400/413/415）。

**4. 成绩列前端计算**
总分 = items 中 `is_correct === true` 计数；选择题 = `has_options===true` 且正确计数；填空题 = `has_options===false` 且正确计数。模式2/3 无 `has_options` → 选择题/填空题显示"—"、只亮总分。`manual_review` 或任一 `review_needed` → 状态徽章"需人工复核"。

**5. 角色守卫复用 `ChemAuth`**
`requireAuth()` → `isTeacherLike()` 否则跳 `forbidden.html?side=teacher`（同 exam-v2/panel 模式）。

**6. 轮询生命周期**
`setInterval` 5s；批次进入终态（done/discarded/error）或页面 `visibilitychange` 隐藏时 `clearInterval`。批改触发用一次性 flag 防重复 `run`（后端另有 409 兜底）。

**7. 回链与占位**
`← 主面板` 指向真实教师主入口 `exam-v2.html`；"开始诊断障碍"为 `<a href="diagnosis.html">` 占位。

## Risks / Trade-offs

- [自动批改与仍处理中任务竞态] → 仅当 `total == done` 才触发 `run`，并以一次性 flag 防重复。
- [无考试时落到自判模式、拆列失效] → 页面要求先选考试（模式1），未选时表格降级只亮总分并提示人工复核。
- [保存后重复提交 409/数据漂移] → 保存成功即禁用保存按钮并刷新结果；`BATCH_ALREADY_SAVED`/`SAVE_INTEGRITY_FAILED` 错误体直接提示。
- [轮询过度请求] → 5s 间隔 + 终态/隐藏页即停，符合后端调度同频。
- [`has_options` 仅模式1] → 规格已声明无考试时不强制携带，前端按缺失容错。
