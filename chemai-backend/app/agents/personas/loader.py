"""Persona 加载（doc 30 §4.2 / spec Persona 工具过滤）。

- 从 `personas/{name}.yaml` 加载 system_prompt / available_skills / data_access。
- `effective_skills(name)`：取「YAML available_skills 白名单」与「TOOL_META 注册的该
  Persona 可用工具」的交集（spec：工具集 SHALL 为两者交集）。
- `validate_personas()`：校验每个 Persona 白名单交集非空、无越权工具。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from app.agents.tools.tool_meta import PERSONAS, TOOL_META, tools_for_persona

_PERSONA_DIR = Path(__file__).resolve().parent


class PersonaLoadError(ValueError):
    """Persona 缺失 / 格式非法。"""


@dataclass
class Persona:
    name: str
    description: str = ""
    system_prompt: str = ""
    available_skills: list[str] = field(default_factory=list)
    data_access: dict[str, Any] = field(default_factory=dict)


def _load_raw(name: str) -> dict:
    path = _PERSONA_DIR / f"{name}.yaml"
    if not path.exists():
        raise PersonaLoadError(f"Persona '{name}' 不存在：{path}")
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return data


def load_persona(name: str) -> Persona:
    """加载单个 Persona（不做工具交集，供原始白名单查看）。"""
    if name not in PERSONAS:
        raise PersonaLoadError(f"未识别的 Persona: {name}（允许: {', '.join(PERSONAS)}）")
    data = _load_raw(name)
    return Persona(
        name=str(data.get("name") or name),
        description=str(data.get("description") or ""),
        system_prompt=str(data.get("system_prompt") or ""),
        available_skills=[str(s) for s in (data.get("available_skills") or [])],
        data_access=dict(data.get("data_access") or {}),
    )


def effective_skills(name: str) -> list[str]:
    """Persona 实际可用工具：YAML 白名单 ∩ TOOL_META 注册工具（有序）。"""
    persona = load_persona(name)
    allowed = set(tools_for_persona(name))
    return [s for s in persona.available_skills if s in allowed]


def unknown_skills(name: str) -> list[str]:
    """白名单中未注册的工具（后续切片占位，非错误，仅告警）。"""
    persona = load_persona(name)
    return [s for s in persona.available_skills if s not in TOOL_META]


def validate_personas() -> list[str]:
    """编译时校验所有 Persona：交集非空且无越权工具。返回硬性问题，空=通过。

    越权 = 工具已注册（TOOL_META）但不在该 Persona 允许集 → 权限提升，硬性错误。
    未注册工具（后续切片占位）仅告警，见 unknown_skills。
    """
    problems: list[str] = []
    for name in PERSONAS:
        persona = load_persona(name)
        allowed = set(tools_for_persona(name))
        leaked = [s for s in persona.available_skills if s in TOOL_META and s not in allowed]
        if leaked:
            problems.append(f"Persona {name} 越权工具（不在 TOOL_META 该角色集）: {leaked}")
        if not effective_skills(name):
            problems.append(f"Persona {name} 白名单∩注册工具交集为空")
    return problems
