## 1. 后端：考试列表与班级端点

- [x] 1.1 新增 `ExamService.list_exams(page, page_size)`（id 倒序、class_name join、question_count 统计）与 `GET /api/exam` 路由（exam:read），返回 `{total,page,page_size,items}`；验证 `tests/unit/test_exam_api.py` 新增列表用例通过
- [x] 1.2 新增 `GET /api/classes` 路由返回班级 id/name 列表；验证 `tests/unit/test_exam_api.py` 新增班级列表用例通过
- [x] 1.3 `ExamBankService.list_sets` 支持「预设 + 我的」并集（teacher_id 时 `in_([0, teacher_id])`，is_preset 置顶）；验证 `tests/unit/test_exam_bank_service.py` 新增并集用例通过
- [x] 1.4 `question_dict` 增补 `audit_status` 字段；验证序列化相关测试通过且断言含 audit_status
- [x] 1.5 全量后端测试通过；验证 `cd chemai-backend && pytest tests/ --tb=short -q` 无回归

## 2. 前端：api.js 真实路由对账

- [x] 2.1 `api.js` 替换 mock 路由：`getExamSetDetail`（替换 searchQuestions）、historical 改 `/api/exam-bank/historical`、exams 改 `GET /api/exam`、删除无端点的 `updateExam` PATCH；验证浏览器 Network 无 404/405
- [x] 2.2 新增 api 方法：`importQuestions`、`addExamQuestions`、`removeExamQuestion`、`publishExam`、`startGrading`、`finalizeExam`、`archiveExam`、`exportExam`（fetch blob）；验证方法签名与端点一致，浏览器可调用成功

## 3. 前端：Tab 2 题库管理重构

- [x] 3.1 左侧文件夹侧栏（`GET /api/exam-bank/exam-sets`，预设置顶 + 我的）+ region/year 筛选条 + 分页控件；验证浏览器目录加载、筛选/翻页生效
- [x] 3.2 题目卡片缩略网格：选中文件夹显示卡片（`GET /api/exam-bank/exam-sets/{id}`），含审核状态角（audit_status）、题型标签（options 推断）、难度徽章、KaTeX 题干与知识点标签；验证浏览器网格渲染正确
- [x] 3.3 批量导入：勾选多题 → `POST /api/exam-bank/exam-sets/{id}/import-questions`，展示 added/skipped 并刷新题目数；验证浏览器导入统计与卡片网格刷新
- [x] 3.4 Tab 1「保存到题库文件夹」下拉 + 批准入库时导入选中文件夹（方案1：批准即入库，未选文件夹时前端阻止；先 import 后 approve，import 跳过 blocked 与 approve 一致）；验证选定文件夹后批准入库提示保存结果、文件夹题目数 +1

## 4. 前端：Tab 4 考试列表六态对齐

- [x] 4.1 六态 chip（Draft/Published/InProgress/Grading/Completed/Archived 文案+配色），接 `GET /api/exam`；验证浏览器列表真实数据 + 六态标签正确
- [x] 4.2 每态条件按钮矩阵（Draft=编辑/发布/删除；Published|InProgress=开始阅卷/导出；Grading=完成统计/导出；Completed=归档/导出/看结果；Archived=导出）调用对应端点；验证浏览器每态按钮集合正确、状态流转后刷新
- [x] 4.3 编辑抽屉：`GET /api/exam/{id}/questions` 列出题目，支持从题库/真题选入（add_questions）与移除单题；验证浏览器抽屉加/移题后列表刷新。改进：add_questions 返回 `skipped_reasons`（逐题跳过原因，如"已被考试「期末」占用"），前端抽屉 toast 展示跳过原因
- [x] 4.4 导出接入：`exportExam` fetch blob 下载 docx/pdf（含/不含答案）；验证浏览器下载文件打开正常、中文不乱码

## 5. 前端：数据层收尾

- [x] 5.1 知识点云改为前端静态列表（后端无 knowledge 端点），Tab 1 仍可渲染；验证浏览器 Tab 1 知识云不报错
- [x] 5.2 `USE_MOCK=false` 并清理 mock 白名单与失效 handler（knowledge/classes 无后端的降级项保留或迁静态）；验证浏览器 Network 无 mock 命中、无演示数据标注

## 6. 集成验证

- [x] 6.1 浏览器黄金路径：登录 → Tab 1 出题（AI 生成 + 批准入库到文件夹）→ Tab 2 文件夹/筛选/批量导入 → Tab 3 真题搜索/选入 → Tab 4 创建/编辑/发布/阅卷/完成/归档/导出 全链路可用；验证无控制台报错、KaTeX 渲染正常（scripts/cdp_golden_path.py 29/29 ALL PASS，2026-08-25 QA 实测）
- [x] 6.2 回归：后端全量 pytest + 前端四 Tab 交互无回归；验证 `pytest tests/ --tb=short -q` 通过、Tab 1 审核卡片与 Tab 3 真题库原功能不受影响
