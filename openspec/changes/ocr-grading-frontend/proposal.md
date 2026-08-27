## Why

OCR 批改管线后端（上传会话状态机、双引擎识别、LLM 批改、分档落库、触发诊断）已全部完成并归档，但教师端缺少一个"上传答题卡 → 看 OCR 进度 → 审核批改结果 → 保存"的页面，只能靠 Agent 对话触发。原型 `stitch-prototypes/pages/ocr.html` 与设计文档 40 §1.6/§1.7 已定义页面形态，本 change 按既有前端约定把它落地为 `frontend/pages/ocr.html` 全闭环页面。

## What Changes

- 新增教师端页面 `frontend/pages/ocr.html`，实现原型布局：页头 + 标题栏（班级→考试联动选择 + 历史批次下拉）+ 拖拽上传区 + 逐文件进度卡片 + 批改结果表格 + 底部操作栏（平均/最高/最低分 + 导出成绩/发送给学生/开始诊断障碍占位链接）。
- **全闭环批改**：上传建批次 → 5s 轮询 OCR 进度 → 全部 done 自动调 `POST /api/grading/run`（携带 exam_id）→ 表格渲染结果 → "保存"调 `POST /api/grading/save` 落库并触发诊断。
- `frontend/pages/js/api.js` 新增 8 个 OCR/grading 方法，含 multipart 上传封装（现有 `request()` 仅支持 JSON）。
- 后端 `app/services/ocr/` 在批改结果 items 注入 `has_options`（有无选项 → 选择题/填空题），供前端拆分成绩列（模式1 有考试时）。
- 角色守卫：教师+ 可访问，学生等越权跳 `forbidden.html?side=teacher`；回链指向真实教师主入口 `exam-v2.html`；自托管字体（不用 Google CDN）。
- "开始诊断障碍"链接到 `diagnosis.html` 占位（该页属后续独立 change，本 change 不创建）。

## Capabilities

### New Capabilities
- `ocr-grading-frontend`: 教师端答题卡 OCR 批改页面——上传建批次、进度轮询、考试绑定与自动批改、结果展示与保存、历史批次切换。

### Modified Capabilities
- `ocr-grading`: 批改判卷 API 的逐题判定 items 新增 `has_options` 字段（标记该题是否有选项，供前端拆选择题/填空题列）。

## Impact

- 前端：`frontend/pages/ocr.html`（新增）、`frontend/pages/js/api.js`（加方法）、复用 `getClasses()`/`getExams()` 与 `ChemAuth` 守卫。
- 后端：`app/services/ocr/batch.py`（`run_grading`/`results_response` 注入题型标注）、`app/services/ocr/grading.py`（items 结构）；`tests/` 补对应断言。
- 不改动 Agent 工具、UploadSession 状态机、诊断管线；`diagnosis.html` 页后续独立 change。
