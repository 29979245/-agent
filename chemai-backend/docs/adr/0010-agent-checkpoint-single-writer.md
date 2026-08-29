# Agent 检查点拓扑约束：AsyncSqliteSaver 单写者（按 11.10 / doc 38）

Agent 阶段（phase-6）决策：LangGraph 检查点使用 `AsyncSqliteSaver`（`agent_checkpoint.db`），**部署固定单 worker / 单实例**；Dockerfile `CMD ["uvicorn", "app.main:app", ...]` 不携带 `--workers` 且不读取 worker 环境变量（无多 worker 变量）。

**Why**：
1. **SQLite 单写者**——AsyncSqliteSaver 在 SQLite 上以文件锁串行化写入；多 worker 并发写同一 `agent_checkpoint.db` 会导致 `database is locked` 竞态，且检查点状态（含审批挂起注册表 D13）是进程内内存态，跨 worker 无法共享。
2. **审批流一致性**——`_pending_approvals` 注册表（`app/agents/agent.py`）为进程内 dict，恢复端点 `pop_pending_approval` 依赖同一进程；多实例下审批可能落在不同 worker 而查不到。
3. **限流一致性**——Token Bucket（`agent_limiter`）同样为进程内状态，单 worker 才能保证全局限流语义。

**调和方式**：Compose 只定义一个 `backend` service（无 `replicas`）；如需水平扩展，须换共享存储的检查点方案（如 Postgres `PostgresSaver` + 分布式审批/限流），文档化而非本阶段实现。

**后果**：单实例吞吐上限受单进程约束；Agent 对话规模增长后需迁移共享存储方案，迁移路径见上。
