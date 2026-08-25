## Why

教师完成出题（文档 46 的审核链路）后，缺少结构化的题库组织与考试全生命周期管理：题目无法归入题库文件夹、考试无状态流转、相似题无法语义检索、试卷无法导出。现有后端仅覆盖认证与题目审核。文档 47 要求本阶段落地题库管理、考试状态机、向量检索、试卷导出四项后端能力，支撑出题工作台 Tab 2（题库管理）与 Tab 4（考试列表）闭环。

## What Changes

- **题库管理（exam-bank）**：新增 `QuestionSet` / `QuestionSetItem` 两级结构模型与 Alembic 迁移；新增 `/api/exam-bank/*` 路由（exam-sets CRUD、papers 地区→年份树、import-questions 批量导入、historical 分页、format-questions）；删除题库仅删 `QuestionSetItem` 关联、题目实体保留；`is_preset` 系统预设文件夹禁删。
- **考试生命周期（exam-lifecycle）**：扩展 `ExamRecord`（新增 `name` / `status` / `question_stats`）；新增 `/api/exam/*` 路由（create、questions 双渠道关联、publish、finalize、results、archive、级联删除）；六态状态机 `Draft → Published → InProgress → Grading → Completed → Archived`，非法转换被拦截，归档为终态只读。
- **向量检索（vector-retrieval）**：新增向量检索服务（ChromaDB 每知识点一向量索引、两段检索、相似题推荐排除自身、MD5 伪向量兜底、维度不匹配自动重建）。
- **试卷导出（paper-export）**：新增导出服务（python-docx 生成 Word、HTML 报告转 PDF），`with_answers` 区分学生版/教师版，中文不乱码。
- **真题库加载器**：按 地区/年份/试卷 JSON 三层目录加载到内存，作为双渠道关联（渠道二历史题复制入库）与向量索引的数据底座。

## Capabilities

### New Capabilities

- `exam-bank`: 题库两级结构（QuestionSet/QuestionSetItem）数据模型与 `/api/exam-bank/*` CRUD、真题库 JSON 加载、批量导入
- `exam-lifecycle`: ExamRecord 扩展与六态考试状态机、`/api/exam/*` 生命周期 API（含发布/完成统计/归档/级联删除）
- `vector-retrieval`: ChromaDB 每知识点一向量索引构建、两段相似题检索（关键词初筛 + 向量精筛）与排除自身
- `paper-export`: Word/PDF 试卷导出与排版规格（A4/SimSun/密封线/题型分节）、学生/教师双版本

### Modified Capabilities

（无）`exam-workbench` 前端规格不变；后端四块能力独立归档，不改变既有能力的行为需求。

## Impact

- **代码**：`chemai-backend/app/services/question/*`（新建 exam_bank / historical / vector / export 服务）、`app/api/v1/exam_bank.py` 与 `exam.py`（新路由）、`app/db/models/exam.py`（ExamRecord 扩列 + 新增 QuestionSet/QuestionSetItem）、`app/db/models/enums.py`（新增 ExamStatus 枚举）、alembic 新迁移、`app/config.py`（真题库目录 / ChromaDB 路径配置）。
- **API**：新增 `/api/exam-bank/*` 与 `/api/exam/*` 两组路由，全部 teacher+ 权限（复用 `require_permission`）。
- **依赖**：`python-docx`（Word 导出）、`chromadb`（向量库，未装时降级）、HTML→PDF 渲染（SimSun 字体注册）。
- **数据**：新增 `question_set` / `question_set_item` 表；`exam_record` 加 `name` / `status` / `question_stats` 列；均通过追加迁移落地。
- **兼容性**：无破坏性变更；复用既有 auth / audit 基础与权限门控。
