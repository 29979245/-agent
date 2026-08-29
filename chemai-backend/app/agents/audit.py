"""审计日志器（doc 30 §10 / design D7）。

- JSONL 追加写入 data/audit/（默认 agent_audit_dir），文件按日切分。
- 内存环形缓冲：deque(maxlen=100)，保留最近 100 条。
- 敏感字段脱敏：password/phone/parent_phone/token/api_key/secret → "***"。
- best-effort 写入：任何异常（磁盘满/权限不足）被静默捕获，绝不阻塞主流程。
"""
from __future__ import annotations

import json
import logging
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)

# 敏感字段集合（doc 30 §10.1）
SENSITIVE_FIELDS = {"password", "phone", "parent_phone", "token", "api_key", "secret"}

RESULT_SUMMARY_MAX_CHARS = 200


def mask_args(args: dict | None) -> dict:
    """深拷贝参数并脱敏：键名命中敏感集合时值替换为 '***'。"""
    if not isinstance(args, dict):
        return {}
    out: dict = {}
    for key, value in args.items():
        if key in SENSITIVE_FIELDS:
            out[key] = "***"
        elif isinstance(value, dict):
            out[key] = mask_args(value)
        elif isinstance(value, list):
            out[key] = [mask_args(v) if isinstance(v, dict) else v for v in value]
        else:
            out[key] = value
    return out


def _iso_timestamp() -> str:
    """ISO 毫秒时间戳（UTC）：datetime 支持 %f，time.strftime 在 Windows 不识别 %f。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def summarize_result(result) -> str:
    """结果摘要：截断至 200 字符；数组显示 '共 N 项'；非字符串转 JSON/str。"""
    if isinstance(result, (list, tuple)):
        return f"共 {len(result)} 项"
    if isinstance(result, dict):
        text = json.dumps(result, ensure_ascii=False, default=str)
    else:
        text = str(result) if result is not None else ""
    if len(text) > RESULT_SUMMARY_MAX_CHARS:
        return text[:RESULT_SUMMARY_MAX_CHARS] + "…"
    return text


class AuditLogger:
    """技能执行审计日志器（进程级单例可复用）。"""

    def __init__(self, audit_dir: str | Path | None = None, maxlen: int = 100) -> None:
        self.audit_dir = Path(audit_dir or settings.agent_audit_dir)
        self.buffer: deque[dict] = deque(maxlen=maxlen)

    def _log_path(self, now: float) -> Path:
        day = time.strftime("%Y-%m-%d", time.localtime(now))
        return self.audit_dir / f"agent_audit_{day}.jsonl"

    def log(
        self,
        *,
        persona: str,
        skill_name: str,
        args: dict | None = None,
        result_summary: str = "",
        duration_ms: float = 0.0,
        error: str | None = None,
    ) -> None:
        entry = {
            "timestamp": _iso_timestamp(),
            "persona": persona,
            "skill_name": skill_name,
            "args": mask_args(args),
            "result_summary": summarize_result(result_summary) if not isinstance(result_summary, str) else result_summary[:RESULT_SUMMARY_MAX_CHARS],
            "duration_ms": round(duration_ms, 1),
        }
        if error is not None:
            entry["error"] = error
        self.buffer.append(entry)
        self._write_best_effort(entry)

    def _write_best_effort(self, entry: dict) -> None:
        """best-effort 写入：任何异常静默捕获，不阻塞主流程（doc 30 §10.1）。"""
        try:
            self.audit_dir.mkdir(parents=True, exist_ok=True)
            with open(self._log_path(time.time()), "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:  # noqa: BLE001 —— 磁盘满/权限不足等均静默
            logger.debug("audit write skipped", exc_info=True)

    def recent(self, n: int | None = None) -> list[dict]:
        """返回最近 n 条（默认全部 ≤100）。"""
        items = list(self.buffer)
        return items[-n:] if n is not None else items


# 进程级默认实例
audit_logger = AuditLogger()
