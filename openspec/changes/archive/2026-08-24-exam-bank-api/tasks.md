## 1. 数据模型与迁移

- [x] 1.1 新增 `ExamStatus` 枚举（draft/published/in_progress/grading/completed/archived）到 app/db/models/enums.py，验证 `pytest tests/unit/test_exam_models.py` 通过
- [x] 1.2 在 app/db/models/exam.py 新增 `QuestionSet` 模型（name/teacher_id/region/year/description/question_count/is_preset/created_at），验证模型可创建且 __tablename__ 正确
- [x] 1.3 新增 `QuestionSetItem` 模型（set_id/question_id/sort_order/added_at）并建立与 QuestionSet、Question 的外键，验证关联查询可用
- [x] 1.4 扩展 `ExamRecord`：新增 name(String)、status(ExamStatus)、question_stats(JSON) 字段，验证既有 test_exam_models.py 全部通过且新字段可读写
- [x] 1.5 生成 alembic 迁移（新建两表 + exam_record 加三列），验证 `alembic upgrade head` 空库可重复执行、`alembic downgrade -1` 可回滚
- [x] 1.6 更新 models/__init__.py 注册 QuestionSet/QuestionSetItem/ExamStatus，验证 `Base.metadata` 收集全部模型、`test_migration.py` 通过

## 2. 真题库加载器

- [x] 2.1 实现真题库加载器（services/question/historical.py）：按 地区/年份/试卷 三层目录解析 JSON 到内存 ExamPaper，验证样例目录加载后题目数与试卷树正确
- [x] 2.2 加载容错：跳过损坏/缺失 JSON 并日志告警不中断，验证坏文件场景仍加载其余题目
- [x] 2.3 历史真题查询：实现按关键词/地区/年份分页查询，验证分页与过滤返回正确

## 3. 题库管理服务与 API

- [x] 3.1 实现 exam_bank 服务（services/question/exam_bank.py）：QuestionSet 创建/列表/详情/删除、QuestionSetItem 批量导入/移除，验证删除非预设保留题目、预设禁删（service 层单测）
- [x] 3.2 新增 api/v1/exam_bank.py 路由：/api/exam-bank/exam-sets CRUD、import-questions、papers、historical，teacher+ 权限，验证集成测试各端点 200 且低权限 403
- [x] 3.3 在 main.py 注册 exam_bank_router，验证 `/api/exam-bank/exam-sets` 可访问（L2 集成测试）
- [x] 3.4 双渠道前置：渠道二从真题库复制题目到 Question 表，验证复制后题目写入题库且可关联

## 4. 考试生命周期服务与 API

- [x] 4.1 实现考试状态机（services/question/exam_state.py）：六态转换表 + 守卫，验证合法转换通过、非法转换（如跳过阅卷直接归档）抛状态机错误
- [x] 4.2 实现 exam 服务：create（Draft）、publish（≥1题校验 + 写 question_stats + total_students）、finalize（统计参考人数/班级统计）、archive（终态只读）、级联删除，验证各 service 层单测
- [x] 4.3 实现题目关联：渠道一直接关联 + 渠道二真题复制关联、查看/移除，验证双渠道各场景
- [x] 4.4 新增 api/v1/exam.py 路由：/api/exam/create、/{id}/questions（POST/GET/DELETE）、/{id}/publish、/{id}/finalize、/{id}/archive、/{exam_id}/results、/{exam_id}/result/{student_id}、DELETE /{exam_id}，teacher+ 权限，验证 L2 集成测试
- [x] 4.5 main.py 注册 exam_router，验证全生命周期按状态机流转 200、非法转换 400

## 5. 向量检索服务

- [x] 5.1 实现向量服务初始化（services/question/vector.py）：ChromaDB PersistentClient + collection exam_questions，ChromaDB 未装时降级 None，验证降级路径不抛异常
- [x] 5.2 实现索引构建：每知识点一向量（::kp-N）、replace/append 模式、维度不匹配清库重建、MD5 伪向量兜底，验证样例题目索引后可查询
- [x] 5.3 实现两段检索：关键词 Top-20 初筛 + 候选内向量精筛 Top-K，排除自身，验证返回相似度降序且不含检索题本身
- [x] 5.4 集成：import-questions/保存入库后增量 append 同步，验证新题可被检索到

## 6. 试卷导出服务

- [x] 6.1 实现 Word 导出（services/question/export.py）：python-docx 排版（A4/边距/SimSun/密封线/题型分节/化学式下标富文本），验证生成 docx 可打开且含分节标题与下标
- [x] 6.2 实现 with_answers 双版本：学生版无答案、教师版红答案+绿解析+「（含答案版）」，验证两版本内容差异正确
- [x] 6.3 实现 HTML 报告与 PDF：generate_report_html（教师/学生版）+ 转 PDF + SimSun 注册，验证中文不乱码、统计卡/TOP5/知识点分布存在

## 7. 配置与全链路整合

- [x] 7.1 config.py 增加真题库目录、ChromaDB 路径配置，.env.example 同步，验证配置读取正确
- [x] 7.2 启动时加载真题库并输出 `[ExamBank] 从 N 个文件加载了 M 道真题`，验证启动日志
- [x] 7.3 全链路冒烟：题库CRUD → 考试创建/关联/发布/阅卷/完成/归档 → 相似题检索（排除自身）→ 导出 Word/PDF，验证集成测试全绿
- [x] 7.4 pre-commit 门禁：`pytest tests/ --tb=short -x` 全量通过
