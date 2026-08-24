## Why

后端四维审核引擎与审核工作流 API（audit-engine-api）已落地并归档，但教师端出题工作台没有可用前端。课程阶段 46《出题工作台与四维审核》要求实现 `exam-v2.html` 出题工作台页面——Vue 3 CDN Tab 切换 + Tailwind CSS + KaTeX 化学公式渲染，**四个 Tab 全量实现**（出题工作台 / 题库管理 / 历史真题库 / 考试列表），闭环「出题 → 双层审核 → 教师批准入库」及题库/真题/考试管理。静态原型 `stitch-prototypes/pages/exam-v2.html` 已与设计文档 25/36/40 对齐视觉与交互，可作为实现参考。

## What Changes

- 在 `chemai-backend/frontend/pages/exam-v2.html` 新建出题工作台页面（Vue 3 CDN 零构建 + Tailwind CSS CDN + KaTeX + marked），由 FastAPI 静态 serve（`/pages/exam-v2.html`）。
- **Tab 1 出题工作台**三子模式：
  - **AI 生成**：题型/难度/知识点配置 → `POST /api/question/generate`（`source=ai`）→ 渲染双层审核报告。
  - **手动录入**：题干/选项/答案/解析/知识点/难度/方程式表单 → 只过方程式级四维校验（`source=manual`）。
  - **OCR 导入**：题目图片上传 → 只过方程式级硬闸（`source=ocr`）。
- **Tab 2 题库管理**：题库文件夹下拉 + 新建/删除 + 题目卡片网格 + 批量操作（加入考试/移除）。
- **Tab 3 历史真题库**：地区→年份→试卷树形浏览 + 关键词搜索 + 真题卡片 + 选题操作（设为变体蓝本/加入考试）。
- **Tab 4 考试列表**：创建考试 + 考试卡片列表（状态标签：草稿/组卷中/已发布/进行中/已完成）+ 操作菜单（编辑/发布/导出/删除）。
- 题目卡片渲染 `audit_report`：方程式级四维徽标（系数配平/反应条件/产物正确性/分子结构）+ 题目级综合分 + 整体状态 + RAG 溯源标签 + 陷阱提示；化学式用 KaTeX + mhchem 渲染。
- 卡片操作对接现有审核 API：批准入库（approve）、打回重生成（regenerate，仅 warning/blocked）。
- 登录态接入现有 `/api/auth` 认证，携带 Bearer token。

### 数据策略（后端缺口 → mock 适配层）

后端当前只落地了 auth 与 audit 两组 API；Tab 2/3/4 端点（`/api/exam-bank/*`、`/api/question/historical`、`/api/exam/*`、`/api/knowledge/list`、`/api/classes`）尚未实现。前端 `api.js` 采用 `USE_MOCK` 开关：已落地接口走真实请求，未落地接口返回带「演示数据」标注的 mock 结果，保证四个 Tab 均可交互走查；后续后端端点落地后仅需把 `USE_MOCK` 置为 false，前端结构不变。

### 非目标（本次不做，列为后续变更）

- 学生端 / 家长端页面。
- 后端批量出题（quantity）、题型组合、变体蓝图（`variant_source` / `variant_qid`）——本次 AI 模式一次提交一道题。
- RAG 向量检索 / 三层搜索的后端实现——只展示 mock/审核报告里的溯源标签，不实现检索链路。
- 真实 OCR 识别引擎调用——本次用上传占位 + 识别按钮，识别结果走 mock。

## Capabilities

### New Capabilities

- `exam-workbench`: 教师出题工作台前端——四 Tab（出题/题库/真题/考试）、三子模式出题、双层审核报告展示、批准/重生成、题库/真题/考试管理。

### Modified Capabilities

- 无（纯前端新增，后端 audit 能力不变；仅补最小 CORS 与 StaticFiles 静态服务配置，属实现细节，不在 spec 层）。

## Impact

- 新增 `chemai-backend/frontend/` 目录（`pages/exam-v2.html`），由 FastAPI 静态 serve。
- 消费 `chemai-backend` 的 `POST /api/question/generate`、`POST /api/question/{id}/approve`、`POST /api/question/{id}/regenerate`、`/api/auth/login`。
- **CORS / 静态服务**：后端当前未注册 `CORSMiddleware` 也未挂载 StaticFiles——需为后端补充最小 CORS 配置 + 静态目录挂载（实现细节，随本变更落地）。
- 复用审核 API 的 `source` 触发范围语义（ai 两层 / manual、ocr 方程式级）。
- 依赖：Vue 3、Tailwind CSS、KaTeX + mhchem、marked（全部 CDN）。
- 提交规范：Conventional Commits，scope 用 `ui`。
