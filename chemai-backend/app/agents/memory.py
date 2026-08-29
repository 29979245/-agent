"""记忆系统（doc 30 §9 / design 用户点名组件）。

三层记忆架构：
- 工作记忆（working）：内存滑动窗口，固定容量 20 条，满时丢最旧（§9.1）。
- 情景记忆（episodic）：本次对话产生的结构化事件（诊断结果、考试记录），随请求销毁（§9.1）。
- 学生档案（profile）：跨请求持久，每请求从数据库查询注入 System Message（§9.1）。
- 长期记忆（long-term / SqliteStore）：跨会话键值存储——学生诊断历史（最近 5 条）、教师偏好（§9.5），
  写入 best-effort（失败不阻塞主流程）。
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
from collections import deque
from pathlib import Path
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

WORKING_MEMORY_MAXLEN = 20
DIAGNOSIS_HISTORY_MAX_ITEMS = 5


class WorkingMemory:
    """工作记忆：固定容量滑动窗口。"""

    def __init__(self, maxlen: int = WORKING_MEMORY_MAXLEN) -> None:
        self._messages: deque[dict] = deque(maxlen=maxlen)

    def add(self, message: dict) -> None:
        self._messages.append(message)

    def clear(self) -> None:
        self._messages.clear()

    @property
    def messages(self) -> list[dict]:
        return list(self._messages)

    def __len__(self) -> int:
        return len(self._messages)


class EpisodicMemory:
    """情景记忆：本次对话的关键事件（诊断结果、考试记录等）。"""

    def __init__(self) -> None:
        self._events: dict[str, Any] = {}

    def record(self, key: str, value: Any) -> None:
        self._events[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        return self._events.get(key, default)

    def as_system_message(self) -> dict | None:
        if not self._events:
            return None
        lines = [f"{k}: {json.dumps(v, ensure_ascii=False, default=str)}" for k, v in self._events.items()]
        return {"role": "system", "content": "本次对话已产生的关键事件：\n" + "\n".join(lines)}


class LongTermStore:
    """长期记忆 SqliteStore（doc 30 §9.5）：agent_memory.db。

    键为 `{namespace}:{key}`（如 `student_diagnosis:2024001`、`teacher_pref:5`）。
    写入 best-effort：任何异常静默捕获，不阻塞主流程。
    """

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = str(db_path or settings.agent_memory_db)
        self._lock = threading.Lock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        return conn

    def _init_schema(self) -> None:
        try:
            with self._lock:
                with self._connect() as conn:
                    conn.execute(
                        "CREATE TABLE IF NOT EXISTS kv_store ("
                        "namespace TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, "
                        "updated_at REAL NOT NULL, PRIMARY KEY (namespace, key))"
                    )
        except Exception:  # noqa: BLE001 —— best-effort
            logger.debug("long-term store init skipped", exc_info=True)

    def put(self, namespace: str, key: str, value: Any) -> bool:
        """写入（best-effort）。返回是否成功。"""
        try:
            payload = json.dumps(value, ensure_ascii=False, default=str)
            with self._lock:
                with self._connect() as conn:
                    conn.execute(
                        "INSERT INTO kv_store (namespace, key, value, updated_at) VALUES (?, ?, ?, ?) "
                        "ON CONFLICT(namespace, key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                        (namespace, key, payload, __import__("time").time()),
                    )
            return True
        except Exception:  # noqa: BLE001
            logger.debug("long-term store put skipped", exc_info=True)
            return False

    def get(self, namespace: str, key: str, default: Any = None) -> Any:
        try:
            with self._lock:
                with self._connect() as conn:
                    row = conn.execute(
                        "SELECT value FROM kv_store WHERE namespace=? AND key=?",
                        (namespace, key),
                    ).fetchone()
            return json.loads(row[0]) if row else default
        except Exception:  # noqa: BLE001
            logger.debug("long-term store get skipped", exc_info=True)
            return default

    # ---- 学生诊断历史（最近 5 条，doc 30 §9.5） ----
    def push_student_diagnosis(self, student_id: int | str, diagnosis: dict) -> bool:
        ns = "student_diagnosis"
        history = self.get(ns, str(student_id), [])
        if not isinstance(history, list):
            history = []
        history.append(diagnosis)
        history = history[-DIAGNOSIS_HISTORY_MAX_ITEMS:]
        return self.put(ns, str(student_id), history)

    def student_diagnosis_history(self, student_id: int | str) -> list[dict]:
        history = self.get("student_diagnosis", str(student_id), [])
        return history if isinstance(history, list) else []

    # ---- 教师偏好（doc 30 §9.5） ----
    def teacher_pref(self, teacher_id: int | str, default: Any = None) -> Any:
        return self.get("teacher_pref", str(teacher_id), default)

    def set_teacher_pref(self, teacher_id: int | str, pref: dict) -> bool:
        return self.put("teacher_pref", str(teacher_id), pref)


class MemorySystem:
    """组合三层记忆（工作/情景/档案）+ 长期存储，供 ContextManager 组装消息。"""

    def __init__(self, long_term_store: LongTermStore | None = None) -> None:
        self.working = WorkingMemory()
        self.episodic = EpisodicMemory()
        self.long_term = long_term_store or LongTermStore()

    # 学生档案由注入的 loader 提供（同步 DB 查询由工具/端点层驱动）
    student_profile: dict | None = None

    def bind_student_profile(self, profile: dict | None) -> None:
        self.student_profile = profile

    def profile_system_message(self) -> dict | None:
        """学生档案 System Message（§9.1：视为权威背景，非对话事实）。"""
        if not self.student_profile:
            return None
        content = "学生档案：" + json.dumps(self.student_profile, ensure_ascii=False, default=str)
        return {"role": "system", "content": content}

    def reset_episodic(self) -> None:
        self.episodic._events.clear()
