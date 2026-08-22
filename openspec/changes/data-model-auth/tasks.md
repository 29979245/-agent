## 1. 基础设施

- [x] 1.1 创建 `app/db/base.py`（DeclarativeBase）与 `app/db/session.py`（create_engine：JSON serializer、`PRAGMA foreign_keys=ON`、WAL），验证引擎初始化无异常且 JSON 字段可序列化往返
- [x] 1.2 创建 `tests/conftest.py`（内存 SQLite + create_all + yield session + 回滚清理），验证 pytest 能发现并运行首个模型测试

## 2. 组织链模型（6）

- [x] 2.1 School 模型（名称/地区/地址/电话/当前学期）与 CRUD + 约束测试
- [x] 2.2 Grade 模型（FK school + 名称/学年）与 CRUD 测试，验证外键依赖链 School→Grade
- [x] 2.3 Class 模型（FK grade + 名称/班主任/学生数/学段/学科）与 CRUD 测试，验证 Grade→Class
- [x] 2.4 Teacher 模型（FK school + 姓名/手机/status 枚举/子角色枚举）与 CRUD + 状态测试
- [x] 2.5 Student 模型（FK class + 障碍画像 JSON/练习追踪/6 位绑定码）与 CRUD + JSON 往返测试（障碍画像 + 学习计划两类 JSON 往返；MutableDict 原地修改后持久化，避免静默丢写）
- [x] 2.6 Account 模型（username 唯一 + password_hash + role/role_id）与 CRUD + 唯一约束测试，验证身份链挂接 + role↔role_id 指向类型一致不变量（应用层测试，SQLite 无外键约束）

## 3. 教学链模型（3）

- [x] 3.1 ExamRecord 模型（FK class + 考试类型枚举/日期/参考人数/统计 JSON）与 CRUD + 枚举约束测试
- [x] 3.2 Question 模型（正文/选项/答案/解析/知识点/难度枚举/来源枚举/审核状态 JSON）与 CRUD 测试
- [x] 3.3 StudentAnswer 模型（FK 学生/题目/考试 + 作答/是否正确/barrier_type/连续计数）与 CRUD + 级联删除测试

## 4. 复习链模型（2）

- [x] 4.1 ReviewTask 模型（FK 学生/题目 + 6 级状态/任务状态）与 CRUD + 状态流转测试
- [x] 4.2 ReviewHistory 模型（FK 复习任务 + 级别/日期/结果）与 CRUD + 级联删除测试

## 5. 家长链模型（3）

- [x] 5.1 Parent 模型（姓名/手机/邮箱/密码哈希）与 CRUD 测试
- [x] 5.2 StudentParentBinding 模型（FK 家长/学生 + 6 位绑定码/关系/状态/唯一约束）与 CRUD + 绑定码校验测试
- [x] 5.3 ParentNotification 模型（FK 家长 + 通知类型/标题/正文/已读）与 CRUD 测试

## 6. OCR 链模型（3）

- [x] 6.1 UploadSession 模型（状态机枚举/降级标记/乐观锁版本）与 CRUD + 状态流转测试
- [x] 6.2 OCRTask 模型（FK 会话 + 进度/结果/确认/错误）与 CRUD + pending→processing→done/failed 测试
- [x] 6.3 StudentSubmission 模型（FK 考试/会话 + 图片/答案列表/总分/时间）与 CRUD 测试

## 7. JWT 认证

- [x] 7.1 实现 `app/core/security.py`：HS256 编解码 + 签名（hmac/hashlib/base64 + 时间校验），验证编码/解码往返测试通过
- [x] 7.2 JWT 六场景测试：编解码 / 签名验证 / 过期 / 篡改 / 格式畸形 / access-refresh 互用（type 混淆），验证六类测试全绿
- [x] 7.3 密码 PBKDF2 哈希与校验（stdlib，盐+迭代），验证正确/错误密码测试通过
- [x] 7.4 登录端点 `POST /api/auth/login` + `POST /api/auth/refresh`，验证成功签发/密码错误/刷新/待审核教师拒绝四场景测试通过
- [x] 7.5 家长登录端点 `POST /api/parent/login`（phone + bind_code），验证成功/绑定码错误测试通过

## 8. RBAC 权限

- [x] 8.1 `app/core/permissions.py` 实现 ROLE_PERMISSIONS 矩阵（5 角色 × 10 资源 × 4 操作，parent 不入矩阵），验证矩阵断言 + 家长默认拒绝测试通过
- [x] 8.2 PermissionChecker 资源级检查（返回用户上下文/无权拒绝，parent 对矩阵资源默认拒绝），验证有权限/无权限测试通过
- [x] 8.3 `@require_permission` 装饰器端点级声明，验证越权返回 403/错误码 PERMISSION_DENIED 测试通过

## 9. HTTP 中间件

- [x] 9.1 全局认证中间件（白名单前缀放行 + Bearer 校验 + 401），验证白名单/缺失 token/无效 token 三场景测试通过
- [x] 9.2 `app/core/exceptions.py` 统一错误码（401/403/404/422/500 + detail/error_code/suggestion），验证错误响应格式测试通过
- [x] 9.3 认证/鉴权事件结构化日志（登录成功/失败、refresh、中间件 401 拒绝带原因），验证关键事件均产生日志

## 10. Alembic 迁移

- [ ] 10.1 `alembic init` + env.py 配置（指向 Base.metadata 与主库 URL），验证 `alembic upgrade head` 无报错
- [ ] 10.2 生成初始迁移并执行 upgrade，验证 17 张表全部创建 + 重复执行幂等；对真实文件库跑冒烟（17 表可查询、热 FK 列索引存在、插入/删除往返）

## 11. 集成验证

- [ ] 11.1 全量 `pytest tests/` 通过（模型/JWT/权限/中间件/迁移），验证无失败用例
- [ ] 11.2 运行 `run_evals --tier l1` 通过（L1 单元 ≥95%），验证后打 `evals-ok` 标签；每个模型/组件已按原子提交（conventional commits：model/auth/permission/guard 等 scope）
