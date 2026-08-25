"""Evals 通用 IO：JSONL Golden 数据集读取。"""

import json
from pathlib import Path


def load_jsonl(path: Path) -> list[dict]:
    cases: list[dict] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    return cases
