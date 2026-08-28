# Webhook 投递签名协议：canonical JSON + HMAC-SHA256 + 时间戳头 + 指数退避重试 ≤3

设计文档 33 §10.3 定义 7 种 Webhook 事件类型，但未规定投递安全协议。本阶段决策：**注册时记录 URL + secret（列表掩码显示），投递时对 canonical JSON（sort_keys + 紧凑分隔）计算 HMAC-SHA256 签名，随 `X-Webhook-Signature` 与 `X-Webhook-Timestamp` 头一并发出，接收方用 secret 验签；失败按指数退避重试 ≤3 次，结果落库到 WebhookRegistration。**

**Why**：
1. **防伪造与重放**——secret 只有注册方与 ChemAI 共享，验签保证请求确由本系统发出；时间戳头让接收方抵御重放（过期丢弃）。
2. **canonical JSON 是签名稳定的前提**——字段排序 + 紧凑分隔保证同一 payload 序列化字节稳定，否则密钥交换/排序差异会导致验签失败。
3. **重试需有界且可见**——无限重试会拖垮请求链路（尤其同步触发场景），≤3 次 + 指数退避在「尽力投递」与「不阻塞主流程」间取平衡；状态落库供管理员排查。

**调和方式**：`app/services/integration/webhook_service.py`——`canonical_json`（sort_keys + separators）、`signing_string = f"{timestamp}.{body}"`、`sign`（HMAC-SHA256 hexdigest）、`verify_signature`（`hmac.compare_digest`）；`_deliver_to_registration` 重试 `backoff_base * 2^attempt`（请求路径内 `backoff_base=0` 不 sleep，真实投递失败由调度/手动重试兜底）；`emit_event` 吞全部异常保证埋点不阻断业务主流程。

**后果**：接入方需按同协议验签（样例：`sign("secret-key-123", "1724803200", '{"name":"张三","student_id":7,"warning_type":"score_drop"}') == "b5bd92..."`）；未接入验签的接收方仅做 HMAC 校验，签名仅为完整性保证，不上加密通道（HTTPS 由部署层保证）。
