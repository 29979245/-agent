## 1. 家长认证+绑定

- [ ] 1.1 实现 `app/core/parent_auth.py::require_parent`（解码 token → 断言 role=parent → 解析 parent_id），单测覆盖 parent 放行 / 非 parent 403 / 无令牌 401
- [ ] 1.2 实现 `require_bound_child(student_id)`（active 绑定校验），单测覆盖 active 放行 / 未绑定 403"未绑定该学生" / 学生不存在 404
- [ ] 1.3 实现 `POST /api/parent/bind`，单测覆盖：绑定成功并消费 `Student.bind_code` / 码不匹配 400 / 已存在 active 绑定 400 / 复用 inactive 历史行恢复 active / 学生不存在 404
- [ ] 1.4 实现 `DELETE /api/parent/bind/{binding_id}`（软删置 inactive），单测覆盖本人解绑成功 / 越权解绑被拒
- [ ] 1.5 实现 `GET /api/parent/children`，单测覆盖返回 active 绑定子女概要 / 无绑定返回空列表
- [ ] 1.6 绑定所有权守卫（解绑/标记他人通知已读须为本家长），单测覆盖越权解绑与越权已读返回 404/403

## 2. 周报+通知

- [ ] 2.1 升级 `NotificationType` 枚举 5 类（weekly_report/score_alert/learning_plan/reminder/daily_report），为 `ParentNotification` 加 `created_at`（DateTime default=utcnow, index）、为 `Student` 加 `weekly_report`(JSON)+`weekly_report_week`(date) 列，生成 alembic 迁移，迁移内存量映射（message→daily_report、warning→score_alert、report→weekly_report），单测断言模型字段与存量值转换
- [ ] 2.2 实现 `app/services/parent_service.py` 家长视角聚合（复用 report_service 底层 helper），单测断言统计卡/知识掌握/时间线字段齐全且响应不含 bind_code 与 teacher_comment
- [ ] 2.3 实现 `GET /api/parent/child/{student_id}/report`，单测覆盖绑定子女返回 / 未绑定 403 / 学生不存在 404
- [ ] 2.4 实现 `app/services/weekly_report_service.py`（文档 33 §7 prompt 模板 + `FallbackDiagnosisLLMClient` + `extract_json` + 纠错重试≤3），单测覆盖 JSON 解析 / 无效响应纠错 / no_data 分支 / 降级错误信号
- [ ] 2.5 实现周报查询与生成端点（`GET .../weekly` 懒生成、`POST .../weekly/generate` 手动、周内去重），单测覆盖懒生成 / 去重返回缓存 / 手动生成 / 无数据 no_data=true
- [ ] 2.6 实现 `POST /api/parent/child/{student_id}/report/ai-summary`，单测覆盖成功返回通俗解读 / LLM 失败返回可读错误
- [ ] 2.7 实现通知 API（`GET /api/parent/notifications` 分页倒序、parent_id 取自 token、`PUT /api/parent/notifications/{notification_id}/read`），单测覆盖分页排序 / 越权 parent_id 被忽略 / 本人已读 / 标记他人通知 404/403
- [ ] 2.8 同步 `daily.py`（message→daily_report）与 `early_warning.py`（warning→score_alert）发送值，更新既有测试（test_daily_practice.py、test_warning_dedup_notify.py），并在 APScheduler 加周一 08:00 UTC 周报生成 job（仅遍历有 active 绑定家长的学生），单测覆盖 job 遍历范围

## 3. LMS+数据权限

- [ ] 3.1 实现 `WebhookRegistration` 模型与 `WebhookEventType` 枚举（7 类型）+ `integration_config` 表，生成迁移，单测断言模型字段与事件类型约束
- [ ] 3.2 实现 `app/services/integration/webhook_service.py` 签名投递（HMAC-SHA256、参数排序拼接、时间戳头、指数退避重试≤3、结果落库），单测锁定签名向量与重试行为
- [ ] 3.3 实现 Webhook API（`GET/POST /api/integration/webhooks`、`DELETE /api/integration/webhooks/{event_type}`、`POST /api/integration/webhooks/trigger/{event_type}`），单测覆盖注册/列表(secret 掩码)/取消/手动触发/不支持类型 400
- [ ] 3.4 实现 LMS 配置与同步 API（`GET/POST /api/integration/lms/config`、`POST /api/integration/lms/sync` 状态占位），单测覆盖配置存取与无连接器同步不报错
- [ ] 3.5 在 `early_warning.py`（warning.triggered）与 `daily.py`（practice.assigned）接入 emit 埋点，单测断言事件触发与既有流程不互扰
- [ ] 3.6 数据权限隔离集成验证：L2 测试断言全部家长数据端点守卫不遗漏（未绑定 403 / 越权隔离 / 通知身份取自 token / 非家长令牌 403），并跑通家长登录 → 绑定 → 子女列表 → 报告 → 周报 → 通知列表/已读 全链路 HTTP 级（`pytest tests/integration/` 通过）

## 4. 收尾

- [ ] 4.1 运行 `pytest tests/ --tb=short -x` 全绿，跑 `run_evals --tier all --compare baseline.json` 无劣化阻断
- [ ] 4.2 修订设计文档 33（§二 login 页改绑定码、§五 绑定码端点改 `/api/student/{id}/bind-code`、决策一 改"6位混淆排除集"）+ 补 CONTEXT.md 术语（绑定/绑定码/周报/通俗解读/Webhook）
- [ ] 4.3 记 ADR（家长认证=绑定码凭证、NotificationType 3类→5类、Webhook 签名协议）到 `docs/adr/`
- [ ] 4.4 运行 `graphify update .` 保持知识图谱同步
