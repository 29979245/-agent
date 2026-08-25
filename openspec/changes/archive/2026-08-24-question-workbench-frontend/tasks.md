## 1. 工程骨架与设计系统

- [x] 1.1 创建 `chemai-backend/frontend/pages/exam-v2.html`：引入 Vue 3 CDN、Tailwind CSS CDN（`?plugins=forms`）、KaTeX + mhchem、marked；验证浏览器打开 `http://localhost:8000/pages/exam-v2.html` 无 JS 报错且 #app 挂载成功
- [x] 1.2 搭建四 Tab 导航（出题工作台 / 题库管理 / 历史真题库 / 考试列表，v-show 切换）+ 学术风格样式（纸色背景、Congo Red 主色，沿用原型 CSS 变量 --oxford/--teal/--paper 等）；验证四 Tab 点击切换
- [x] 1.3 在 `chemai-backend/app/main.py` 注册 CORSMiddleware + 挂载 StaticFiles（`frontend/pages/` → `/pages`）；验证 `/pages/exam-v2.html` 可访问且跨源 /health 不被拦

## 2. 认证与 API 层

- [x] 2.1 实现 `frontend/pages/js/api.js`（baseURL 默认 http://localhost:8000、Bearer 注入、401/403/422 错误映射、USE_MOCK 开关）；验证调用 GET /health 返回 ok
- [x] 2.2 实现 mock 适配层：未落地端点（exam-bank、historical、exam、knowledge、classes）返回带「演示数据」标注的 mock 结果；验证 USE_MOCK=false 时走真实请求、true 时走 mock
- [x] 2.3 实现登录页与 js/auth.js（POST /api/auth/login → 存 localStorage token → 角色门控 teacher+）；验证教师登录进入工作台、学生登录被拒提示无权限

## 3. Tab 1 出题工作台三子模式

- [x] 3.1 工作台子模式切换（AI 生成 / 手动录入 / OCR 导入）；验证点击切换对应表单面板
- [x] 3.2 AI 生成子模式：题型多选/难度/知识点检索表单 + 生成按钮，调 POST /api/question/generate（source=ai）；验证提交后拿到 audit_report
- [x] 3.3 手动录入子模式：题干/选项/答案/解析/知识点/难度/化学方程式表单，调 /generate（source=manual）；验证 report.question_level 为 null
- [x] 3.4 OCR 导入子模式：题目图片上传占位 + 识别按钮，调 /generate（source=ocr）；验证只走方程式级、question_level 为 null

## 4. Tab 1 审核报告渲染

- [x] 4.1 QuestionCard 组件：解析 audit_report 渲染 equation_level 四维徽标（系数配平/反应条件/产物正确性/分子结构 三态配色）+ overall_status 大徽标；验证用后端样例 JSON 对照显示
- [x] 4.2 question_level 区块：四维分数 + composite 综合分，question_level 为 null（manual/ocr）时隐藏；验证 ai 显示综合分而 manual 不显示
- [x] 4.3 接入 katex.js 自动渲染（auto-render 对 $、$\ce{}、$$ 分隔符）；验证化学式排版为公式而非原始 \ce 文本
- [x] 4.4 meta.generation_failed 为真时渲染「出题失败（重生成 N 次）」横幅；验证 AI 配平失败场景（source=ai + 未配平内容）触发

## 5. Tab 2 题库管理

- [x] 5.1 题库文件夹下拉 + 新建/删除（GET /api/exam-bank/exam-sets，mock 标注）；验证文件夹列表加载与增删
- [x] 5.2 题目卡片网格（选择文件夹后展示：题干 KaTeX 渲染、选项、答案、知识点标签）；验证切换文件夹刷新题目
- [x] 5.3 批量操作栏（勾选多题 → 批量加入考试 / 从题库移除）；验证勾选与批量操作状态更新

## 6. Tab 3 历史真题库

- [x] 6.1 地区→年份→试卷树形浏览（GET /api/exam-bank/papers，mock 标注）；验证逐级展开
- [x] 6.2 关键词搜索框 + 搜索按钮（GET /api/question/historical，mock 标注）；验证结果列表渲染
- [x] 6.3 真题卡片 + 选题操作（设为变体蓝本 / 加入考试）；验证操作按钮触发对应回调与提示

## 7. Tab 4 考试列表

- [x] 7.1 创建考试表单（名称 + 班级选择，GET /api/classes mock 标注，POST /api/exam/create mock）；验证创建后出现在列表
- [x] 7.2 考试卡片列表：状态标签（草稿/组卷中/已发布/进行中/已完成）+ 操作菜单（编辑/发布/导出/删除）；验证不同状态显示对应标签与操作

## 8. 操作与端到端联调

- [x] 8.1 卡片操作按钮：passed/warning 显示「批准入库」，warning/blocked 显示「打回重生成」；验证分别调 /approve、/regenerate 并刷新卡片状态
- [x] 8.2 批准 blocked 题目时展示后端 400 detail 提示；验证操作被拒且有提示
- [x] 8.3 端到端走查：启动后端 + 浏览器打开 `/pages/exam-v2.html`，四 Tab 各走一条主流程（AI 出题双层报告/题库浏览/真题搜索/考试创建），核对批准入库、重生成；验证无控制台报错
