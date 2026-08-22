## Why

阶段一（环境搭建）已交付 chemai-backend/ 骨架，但 `app/db/models/` 与 `app/core/` 为空，没有任何数据层和认证层。后续 5 个阶段（出题/诊断/OCR/Agent/评测）全部建立在数据模型与认证权限之上——数据模型是所有业务的存储地基，认证权限决定了谁能访问哪些数据。必须先打好这个地基。

## What Changes

- 新增 **17 个 SQLAlchemy 数据模型**，按 5 组依赖链组织：
  组织链 6（School → Grade → Class → Teacher/Student + Account）、考试链 3（ExamRecord → Question → StudentAnswer）、复习链 2（ReviewTask → ReviewHistory）、家长链 3（Parent / StudentParentBinding / ParentNotification）、OCR 链 3（UploadSession → OCRTask / StudentSubmission）
- 5 个枚举类型：`BarrierType`（concept/reading/expression）、`ExamType`、`QuestionSource`、`AuditStatus`、`Difficulty`；**数据库一律存英文代码**，中文标签由 API 层映射
- 5 个 JSON 字段的结构化存取：学生障碍画像、考试错题统计、复习历史、四维审核报告、学生学习计划（SQLite 需配置 `json_serializer/json_deserializer`）
- **JWT 认证**：纯 Python HMAC-SHA256 自实现（不依赖外部库），access token 24h + refresh token 7d，无状态会话，登出仅清客户端缓存
- **RBAC 权限矩阵**：`ROLE_PERMISSIONS`（5 角色 × 10 资源 × 4 操作），三层权限检查：HTTP 中间件（全局 JWT 校验 + 白名单）→ PermissionChecker（资源级）→ `@require_permission` 装饰器（端点级）
- **家长独立认证通道**：`POST /api/parent/login`（phone + bind_code），JWT `role=parent` 且不携带 `school_id`
- **Alembic 迁移初始化**，主库 17 张表可执行 `alembic upgrade head`
- 多租户数据隔离基础：学校→年级→班级层级链作为隔离边界

## Capabilities

### New Capabilities

- `data-models`: 17 个 SQLAlchemy 数据模型、5 个枚举、5 个 JSON 字段、依赖链与删除级联策略、主库 Alembic 迁移
- `auth`: JWT 签发/验证/刷新（HMAC-SHA256 自实现）、统一账户登录、家长独立登录、无状态会话
- `access-control`: ROLE_PERMISSIONS 权限矩阵、三层权限检查（中间件/PermissionChecker/装饰器）、家长"最小可见"数据边界

### Modified Capabilities

无（`openspec/specs/` 当前为空，全部为新能力）。

## Impact

- **代码**：`app/db/models/` 新增 17 个模型（按 org/user/exam/question/diagnosis/review/parent/ocr 分组）；`app/core/` 新增 `security.py`（JWT）、`permissions.py`（矩阵）、`deps.py`（依赖注入）、`exceptions.py`（统一错误码）；`app/api/v1/auth.py` 新增登录端点；`app/db/` 新增 session/base 配置
- **数据库**：主库 `data/chemai.db` 的 schema（17 张表 + 5 枚举约束），通过 Alembic 管理
- **依赖**：无新增外部库（JWT 自实现）；SQLAlchemy/Alembic/FastAPI 已就位
- **测试**：`tests/unit/` 模型 CRUD + 约束 + 枚举断言；`tests/unit/` JWT 四场景（编解码/签名/过期/篡改）；`tests/integration/` 权限矩阵 + 中间件认证流
