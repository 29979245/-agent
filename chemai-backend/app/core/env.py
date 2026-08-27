"""极简 .env 加载：零依赖，把项目根 .env 的 KEY=VALUE 注入 os.environ。

必须在任何读取环境变量的模块（如 core/security 读 JWT_SECRET）之前执行，
故 app/__init__.py 顶部已触发，保证整包导入时最先运行。
"""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent


def load_dotenv_file(path: Path | None = None) -> None:
    """读取 .env 注入 os.environ（仅当该键未被显式设置时）。跳过空行/# 注释，值去外层引号。"""
    p = path or (BASE_DIR / ".env")
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if value and len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            os.environ.setdefault(key, value)
