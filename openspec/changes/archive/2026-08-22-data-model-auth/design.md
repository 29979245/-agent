## Context

阶段一骨架已就位（chemai-backend/），`app/db/models/` 与 `app/core/` 为空。本变更建立全部 17 个数据模型、JWT 认证、RBAC 权限矩阵与 HTTP 认证中间件，作为后续 5 个阶段的地基。动机见 proposal.md。范围经 grilling 对齐：**17 个模型**（教程 45 号验收口径），`TeacherClassSubject`、`TeacherApplication` 及题库/诊断相关实体推迟到阶段三。

## Goals / Non-Goals

**Goals:**
- 17 个 SQLAlchemy 模型按 5 组依赖链落地，外键与级联策略一致
- 自研 JWT（HMAC-SHA256，纯 stdlib），覆盖编解码/签名/过期/篡改/格式畸形/type 混淆六类场景
- ROLE_PERMISSIONS 矩阵 + 三层权限检查（中间件/检查器/装饰器）
- Alembic 初始化迁移，主库 17 张表可执行
- SQLite 下 4 个 JSON 字段的可靠存取（障碍画像/学习计划/错题统计/审核报告；复习历史走表）

**Non-Goals:**
- 行级数据范围过滤（"教师只看任教班级"）——依赖 TeacherClassSubject，阶段三
- 教师入驻审批状态机与申请模型——阶段三
- 题库/知识点/预警相关模型（QuestionSet/HistoricalExam/KnowledgePoint/WarningLog/BarrierConfig）——阶段三/四
- 速率限制、SSE 端点、业务查询端点——后续阶段

## Decisions

### D1: 17 个模型按 5 组依赖链落地
```
组织链 6:  School → Grade → Class → Student   + Teacher + Account
教学链 3:  ExamRecord → Question → StudentAnswer
复习链 2:  ReviewTask → ReviewHistory
家长链 3:  Parent + StudentParentBinding + ParentNotification
OCR 链 3:  UploadSession → OCRTask + StudentSubmission
```
**理由**：与教程 45 号 Step 1 验证口径完全一致（6+3+2+3+3）。推迟的两个认证相关模型不影响本阶段 RBAC（矩阵是角色×资源×操作，不涉及行级过滤）。
**备选**：完整 23 实体一次建齐——被否决，扩大验收风险且无对应功能验证。

### D2: 数据库枚举一律英文代码
`BarrierType`(concept/reading/expression)、`ExamType`、`QuestionSource`、`AuditStatus`(passed/warning/blocked)、`Difficulty`(easy/medium/hard/competition)、`Account.role`(admin/dept_admin/subject_lead/teacher/student/parent)。
**理由**：DB 枚举用稳定英文码抗变动，中文显示是表现层职责，由单一映射模块翻译。
**备选**：中文枚举值——被否决，易受别名/空格/改名影响，迁移脆弱。

### D3: JWT 纯 stdlib 自实现（HMAC-SHA256）
用 `hmac`/`hashlib`/`base64`/`json` 实现 HS256 签名的 JWT 编解码与验证。payload 字段：`user_id`、`role`、`school_id`（家长无此字段）、`type`、`iat`、`exp`。access 24h、refresh 7d。签名密钥取环境变量 `JWT_SECRET`（开发默认值仅限本地，不硬编码），rotation=换密钥重启（沿设计文档 23 §8.1/§9.3）。
**理由**：教程 45 号验收标准明确要求自实现且覆盖四类场景；零外部依赖符合设计文档意图。
**备选**：PyJWT——安全审计更充分，但偏离教程验收；作为未来加固选项记录，切换成本是替换单一编码/解码函数。

### D4: 密码用 stdlib PBKDF2 哈希
`hashlib.pbkdf2_hmac` + 随机盐 + 高迭代次数存储密码，不存明文。
**理由**：与"不引入外部密码学依赖"一致，PBKDF2 是 OWASP 认可的抗暴力破解算法。
**备选**：bcrypt/passlib——更成熟但引入外部依赖；列为此后可选的加固项。

### D5: 统一账户模型
`Account` 表：`username` 唯一、`password_hash`、`role`、`role_id`（指向 Teacher/Student/Parent 主键）。Teacher 表含 `status`（pending/approved/rejected）与 `sub_role`（admin/dept_admin/subject_lead/teacher），登录时按 role 进入对应端。
**理由**：设计文档 34 号"身份链"：一个账户 1:1 关联一种角色记录。
**备选**：独立角色表 + 多态关联——过度设计，当前阶段用 role 字段 + role_id 足够。
**补充**：RBAC 权限判定的唯一事实来源是 `Account.role`；`Teacher.sub_role` 仅表达管理树层级/展示，不参与权限判定（T3）。`role_id` 无法由 SQLite 外键约束（可能指向 Teacher/Student/Parent 之一），以应用层不变量测试保证 role↔role_id 指向类型一致（T2）。

### D6: 三层权限检查
```
Layer1: HTTP 中间件 —— 全局 JWT 校验，白名单前缀放行
        (/api/auth/ /api/agent/ /api/ocr/ /api/classes/ /api/parent/
         /api/knowledge/ /api/exam-bank/ /api/question/ /docs /health ...)
Layer2: PermissionChecker —— (resource, action) × ROLE_PERMISSIONS 矩阵 → 用户上下文
Layer3: @require_permission("resource","action") 装饰器 —— 端点级声明
```
**理由**：设计文档 23 号七章原文，认证/授权/声明职责分离。
**备选**：单一依赖全局中间件——可维护性差，且无法表达端点级差异。
**补充**：`parent` 角色不入 RBAC 矩阵——矩阵仅覆盖 5 角色（admin/dept_admin/subject_lead/teacher/student）；parent 走独立认证路径，对矩阵资源默认拒绝（default-deny）。家长"最小可见"数据边界属行级数据范围过滤，随业务查询端点推迟到阶段三（T1/F2）。

### D7: SQLite JSON 字段
`create_engine` 配置 `json_serializer`/`json_deserializer`，并启用 `PRAGMA foreign_keys=ON`、WAL 模式。JSON 列定为 4 项：`Student.障碍画像`、`Student.学习计划`、`ExamRecord.错题统计`、`Question.审核报告`；复习历史以 ReviewTask→ReviewHistory 表落地（非 JSON，支撑遗忘曲线多行记录）。JSON 列用 SQLAlchemy `MutableDict`/`MutableList` 包装以支持原地修改追踪，避免静默丢写（T4）。
**理由**：教程 45 号明确预警 SQLite 不原生支持 JSON，需引擎层配置。

### D8: 外键删除策略
- 组织链（School/Grade/Class）：删除受限（有子记录则拒绝）
- 教学链：删除 ExamRecord 级联删除 StudentAnswer 与题目关联（设计文档 25 号原文）；删除 Question 受限（有作答则拒绝）
- 复习链：删除 ReviewTask 级联 ReviewHistory
- 家长链：解除 StudentParentBinding 不删除家长与学生记录
- OCR 链：删除 UploadSession 级联 OCRTask 与 StudentSubmission

**理由**：组织链是数据隔离边界，禁止误删；教学链沿 34 号教学链语义级联。

### D9: Alembic 初始化迁移
`alembic init` 后配置 env.py 指向 Base.metadata 与主库 URL；生成单个初始迁移创建 17 张表。单元/集成测试用内存 SQLite + `create_all`（不跑迁移），迁移可执行性单独用真实文件库验证。
**理由**：迁移与测试解耦，TDD 快速迭代；迁移在验收标准 5 单独验证。

### D10: 初始迁移含热 FK 列索引
初始迁移为高频关联查询列建索引：`student.class_id`、`student_answer.student_id`/`question_id`/`exam_id`、`review_task.student_id`、`ocr_task.session_id`、`student_submission.exam_id`、`parent_binding.parent_id`/`student_id`。SQLite 不会自动为 FK 列建索引，空表建索引零成本（F7）。

### D11: 认证/鉴权事件结构化日志
登录成功/失败、refresh、中间件 401 拒绝（带原因：缺失/过期/无效 token）记结构化日志（时间、用户名、角色、事件、原因），作为安全审计痕迹（F5）。

## Risks / Trade-offs

- **手写 JWT 的密码学风险** → 用 TDD 六场景（编解码/签名/过期/篡改/格式畸形/type 混淆）钉住行为；`/plan-eng-review` 复审；未来切 PyJWT 只换一个模块，测试不变
- **SQLite 并发写** → WAL + NORMAL 同步；单校部署量级足够
- **17 vs 23 实体债** → Alembic 增量迁移零成本补表；推迟实体已在 CONTEXT.md 标注
- **枚举中英映射漂移** → 单一映射模块作为唯一事实来源，测试断言全映射
- **认证白名单前缀过宽**（`/api/agent/` `/api/ocr/` `/api/classes/` `/api/parent/` `/api/knowledge/` `/api/exam-bank/` `/api/question/`）→ 沿设计文档 23 §7.1 原文保留（§8.4 家长独立认证路径需要），但属前瞻性安全债：阶段三落业务查询端点时须逐前缀收窄至按端点鉴权（F1）
- **认证端登录门控与业务行为推迟** → teacher status=pending 登录拒绝本阶段落地；"错题创建复习任务""作答连续计数"等业务行为推迟到阶段三/四，其接口仍在模型中（F4）
- **迁移与 create_all 漂移** → 迁移后对真实文件库跑冒烟（17 表 + 索引存在、可读写），后续 schema 变更以迁移为唯一入口，避免 autogenerate 与运行时模型脱节（T5）

## Migration Plan

1. `alembic init alembic`，配置 `env.py`（导入 Base.metadata，指向主库）
2. `alembic revision --autogenerate -m "initial schema"`，人工审查生成脚本
3. `alembic upgrade head`，验证 17 张表 + 5 枚举约束；对真实文件库跑冒烟：17 表可查询、热 FK 列索引存在（D10）、插入/删除往返（T5）
4. 回滚：`alembic downgrade base`（空库重建可随时 `rm data/chemai.db` 后重跑）

## Open Questions

无。已确认决策与推迟范围均在 grilling 中对齐，剩余未知量（题库/预警模型 schema）属后续阶段的既有范围，不影响本变更的规格、方案与任务拆分。
