"""Token Bucket 限流（design D6：挂 stream 端点级，参数从简可后调）。

- TokenBucket：容量 capacity、补充速率 refill_rate（令牌/秒）；consume 失败返回 False。
- TokenBucketLimiter：按 key（user_id）维护独立桶，线程安全。
- agent_limiter：全局单例（由 settings 构建），测试可 monkeypatch 为小容量桶。
"""
from __future__ import annotations

import threading
import time

from app.config import settings


class TokenBucket:
    def __init__(self, capacity: float, refill_rate: float) -> None:
        self.capacity = capacity
        self.refill_rate = refill_rate
        self._tokens = float(capacity)
        self._last = time.monotonic()

    def consume(self, n: float = 1.0) -> bool:
        now = time.monotonic()
        self._tokens = min(self.capacity, self._tokens + (now - self._last) * self.refill_rate)
        self._last = now
        if self._tokens >= n:
            self._tokens -= n
            return True
        return False


class TokenBucketLimiter:
    def __init__(self, capacity: float, refill_rate: float) -> None:
        self.capacity = capacity
        self.refill_rate = refill_rate
        self._buckets: dict[str, TokenBucket] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, n: float = 1.0) -> bool:
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = TokenBucket(self.capacity, self.refill_rate)
                self._buckets[key] = bucket
            return bucket.consume(n)

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()


# 全局单例：速率 12 次/分钟、突发 20 次（测试多次请求不受限，运营后可经 env 调整）
def _build_agent_limiter() -> TokenBucketLimiter:
    per_minute = float(settings.agent_rate_per_minute)
    burst = float(settings.agent_rate_burst)
    return TokenBucketLimiter(capacity=burst, refill_rate=per_minute / 60.0)


agent_limiter = _build_agent_limiter()
