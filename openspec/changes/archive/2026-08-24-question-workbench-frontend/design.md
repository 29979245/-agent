## Context

动机见 proposal.md。本设计覆盖四 Tab 出题工作台页面：Tab 1 出题工作台（三子模式）、Tab 2 题库管理、Tab 3 历史真题库、Tab 4 考试列表，以及题目卡片的审核报告渲染。技术栈与页面落位严格对齐课程阶段 46：Vue 3 CDN + Tailwind CSS CDN + KaTeX + marked，文件在 `chemai-backend/frontend/pages/exam-v2.html`，由 FastAPI 静态 serve（`/pages/exam-v2.html`）。

**现有 API 契约（前端对接面）**

- `POST /api/auth/login` `{username, password}` → `{access_token, refresh_token, token_type, user_id, role, name, school_id}`
- `POST /api/question/generate` `{content, options[], answer, analysis, knowledge_points, difficulty, source}` → `{question_id, audit_report, overall_status, generation_failed}`
- `POST /api/question/{id}/approve` → `{question_id, status: "approved", review_flag}`
- `POST /api/question/{id}/regenerate` `{content?, answer?, analysis?, knowledge_points?}` → `{question_id, audit_report, overall_status}`

**后端缺口（Tab 2/3/4 端点未落地，用 mock 适配层补齐）**

`/api/exam-bank/exam-sets`、`/api/exam-bank/papers`、`/api/question/search`、`/api/question/historical`、`/api/exam/*`、`/api/knowledge/list`、`/api/classes` 后端均未实现。api.js 用 `USE_MOCK` 开关：已落地接口走真实请求，未落地接口返回标注「演示数据」的 mock 结果。

**`audit_report` JSON 结构**（`workflow.build_audit_report`）

```jsonc
{
  "equation_level": {
    "系数配平":  {"status": "passed|warning|blocked", "evidence": "..."},
    "反应条件":  {"status": "..."},
    "产物正确性": {"status": "..."},
    "分子结构":  {"status": "..."},
    "overall_status": "passed|warning|blocked"
  },
  "question_level": {                 // manual/ocr 时为 null
    "科学性": {"score": 0-100, "evidence": "..."},
    "难度匹配": {"score": ..., ...},
    "知识点覆盖": {"score": ..., ...},
    "区分度": {"score": ..., ...},
    "composite": 84.25, "status": "passed|warning|blocked", "reason": "..."
  },
  "overall_status": "passed|warning|blocked",
  "meta": {"regeneration_attempts": 0, "generation_failed": false}
}
```

**约束**：后端当前未注册 `CORSMiddleware` 也未挂载 StaticFiles，跨源请求与 `/pages/*` 静态访问都会被拦；化学公式按后端规范一律 KaTeX/LaTeX 格式（`\ce{...}`）；后端 CLAUDE.md 约定前端提交用 Conventional Commits scope `ui`。

## Goals / Non-Goals

**Goals:**
- 一个零构建的 Vue 3 CDN 单页 `frontend/pages/exam-v2.html`，由 FastAPI 静态 serve，浏览器直接打开连上本地后端。
- 四 Tab 全量：出题工作台三子模式提交并渲染双层审核报告；题库管理文件夹+题目网格+批量操作；历史真题库树形浏览+搜索+选题；考试列表创建+生命周期操作。
- 用 Tailwind CSS CDN + 学术风格自定义 CSS、KaTeX + mhchem 渲染化学式，沿用原型 `exam-v2.html` 的设计系统视觉（doc 36）。

**Non-Goals:**
- 不实现后端批量出题、变体蓝图、RAG 检索——AI 模式一次提交一道题，溯源标签只展示报告/mock 里已有的内容。
- 不实现真实 OCR 识别引擎与 Tab 2/3/4 后端端点——mock 适配层覆盖交互走查，后端落地后切换。
- 不做学生端 / 家长端页面。

## Decisions

### D1：技术栈 Vue 3 CDN（全局构建）+ Tailwind CSS CDN，零构建
从 CDN 引入 `vue.global.prod.js`、`cdn.tailwindcss.com`（`?plugins=forms`）、KaTeX + mhchem、marked。单页 `exam-v2.html`，无打包器、无 vue-router/pinia/TS/.vue SFC。
- **备选**：Vite 工程化——被否：课程阶段 46 明确禁止 npm 构建，前端由 FastAPI 直接静态 serve，无 dev server。
- **理由**：与课程技术栈一致、无构建依赖、原型 CSS 变量可直接复用。

### D2：目录落位 `chemai-backend/frontend/pages/exam-v2.html`
页面与配套 JS 放在后端项目内 `frontend/pages/`，FastAPI 挂载 StaticFiles 以 `/pages/*` 静态 serve。
- **备选**：独立 `chemai-frontend/` 目录——被否：课程规定文件在 `frontend/pages/` 下，FastAPI 直接 serve。
- **理由**：打开 `http://localhost:8000/pages/exam-v2.html` 即可用，无需额外静态服务器。

### D3：API 层、鉴权与 mock 适配层
- `api.js` 维护 `baseURL`（默认 `http://localhost:8000`），所有请求自动带 `Authorization: Bearer <token>`。
- token 存 `localStorage`，`401` 响应清 token 并跳登录页；`403` 弹「无权限」提示；`422` 显示校验错误。
- 登录页调 `/api/auth/login`，仅 `role ∈ {teacher, admin}` 放行进入工作台；学生/家长登录后提示无权限。
- **mock 适配层**：`USE_MOCK` 开关，已落地接口（auth、question/generate/approve/regenerate）走真实请求；未落地接口（exam-bank、historical、exam、knowledge、classes）返回带「演示数据」标注的 mock 结果。
- **理由**：后端 `require_permission("question", "create")` 已保证接口安全，前端只需正确传递 token 并做角色门控；mock 让四 Tab 全程可走查。

### D4：单页四 Tab 组件结构
- 页面级 `Vue.createApp`，`data` 持有 `tab ∈ {workbench, bank, history, exams}`、子模式 `mode ∈ {ai, manual, ocr}` 及各 Tab 数据；`computed` 处理筛选（知识点/搜索）；`mounted` 加载知识点/题库/真题/考试数据。
- 四 Tab 用 `v-show` 切换；Tab 1 内部三子模式切换独立表单面板。
- 工具函数：`getToken()`（localStorage）、`api()`（fetch 封装自动附 Bearer）、`toast()`（Toast 通知）、`renderChem()`（marked 解析 + KaTeX 渲染）。
- **理由**：课程要求单 HTML 内 `Vue.createApp({...})` 模式，四 Tab 纯前端切换无需路由。

### D5：审核报告渲染组件
- 卡片消费 `audit_report`：
  - `equation_level` 四维徽标按 `status` 映射 pass/warn/block 三态配色（绿/黄/红）；
  - `question_level === null`（manual/ocr）隐藏题目级区块，只显示方程式级；
  - `overall_status` 单独大徽标；`meta.generation_failed` 为真时显示「出题失败（重生成 N 次）」横幅；
  - KaTeX 对卡片内 `\ce{...}` / `$\ce{...}$` 内容自动渲染。
- **理由**：报告结构（workflow.py）字段稳定，组件只做纯展示映射，不动后端。

### D6：操作按钮
- 卡片按 `overall_status` 渲染操作：passed/warning →「批准入库」；warning/blocked →「打回重生成」（重生成用弹窗改内容后提交 regenerate）。
- 批准 blocked 时后端返回 400，前端展示后端 `detail` 提示。
- **理由**：对齐后端状态机（approve 仅 passed/warning、regenerate 仅 warning/blocked）。

### D7：CORS 与静态服务（随本变更的最小后端配置）
在 `chemai-backend/app/main.py` 注册 `CORSMiddleware`（允许前端源，开发期 `http://localhost:*` 或 file:// 场景放开），并挂载 StaticFiles 将 `frontend/pages/` 以 `/pages` 路由静态 serve。
- **备选**：前端起代理——被否：零构建 SPA 无 dev server，直接开文件/静态路径更符合课程。
- **理由**：不改任何业务逻辑，仅解锁浏览器跨源访问与 `/pages/*` 页面访问，属「让前端连得上」的最小使能项。

## Risks / Trade-offs

- **CDN 可用性**（Vue/Tailwind/KaTeX/marked 走网络）→ 页面降级显示原始 `\ce{}` 文本；文档注明需联网，本地可下载依赖替换 `<script src>` 为本地路径。
- **CORS/静态未配导致联调失败** → D7 已把最小配置纳入本变更；联调第一步先验证 `/health` 与 `/pages/exam-v2.html`。
- **mock 数据与真实接口语义差异** → mock 结果带「演示数据」标注，接口契约字段对齐 doc 25 的 API 形状；后端落地后置 `USE_MOCK=false` 即切真实。
- **一次一道题体验受限** → 已列为非目标；后续 `question-workbench-batch` 变更补批量/变体。
- **无自动化前端测试** → 本变更以手动验证 + 后端 L2 集成测试（已有 test_audit_api.py 覆盖接口）为主；组件纯展示逻辑，回归靠人工走查。

## Open Questions

无阻塞项。后端缺口（Tab 2/3/4 端点、批量、变体、RAG、真实 OCR）已由课程非目标确认，留待后续变更。
