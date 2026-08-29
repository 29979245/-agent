"""MCP 工具注册表（doc 30 十二 / spec「MCP 工具服务器」，挂载 /api/mcp）。

- mcp_tool 装饰器：注册工具（name/description/args_schema/requires 角色门控）。
- list_mcp_tools：返回全部工具的名称、描述与参数 Schema（pydantic JSON Schema）。
- call_mcp_tool：按名执行——校验参数 Schema、检查角色门控、调用 handler。
- MCPToolNotFound / MCPToolForbidden：未注册名 / 角色越权，由 API 层映射 404/403。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from pydantic import BaseModel


class MCPToolNotFound(RuntimeError):
    """调用未注册的 MCP 工具名。"""


class MCPToolForbidden(RuntimeError):
    """当前角色无权调用该 MCP 工具。"""


@dataclass(frozen=True)
class MCPTool:
    """注册的 MCP 工具元数据：handler 统一签名 `(db, user, **args)`。"""

    name: str
    description: str
    handler: Callable[..., Awaitable[Any]]
    args_schema: type[BaseModel]
    requires: tuple[str, ...] = ()  # 空 = 任意已认证角色可调

    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.args_schema.model_json_schema(),
        }


_REGISTRY: dict[str, MCPTool] = {}


def mcp_tool(name: str, description: str, args_schema: type[BaseModel], requires: tuple[str, ...] = ()):
    """注册 MCP 工具装饰器；重复注册抛 RuntimeError 防静默覆盖。"""

    def decorator(fn):
        if name in _REGISTRY:
            raise RuntimeError(f"MCP 工具重复注册: {name}")
        _REGISTRY[name] = MCPTool(
            name=name,
            description=description,
            handler=fn,
            args_schema=args_schema,
            requires=tuple(requires),
        )
        return fn

    return decorator


def get_mcp_tool(name: str) -> MCPTool:
    tool = _REGISTRY.get(name)
    if tool is None:
        raise MCPToolNotFound(name)
    return tool


def list_mcp_tools() -> list[dict]:
    return [tool.schema() for tool in _REGISTRY.values()]


async def call_mcp_tool(name: str, *, db, user, arguments: dict) -> Any:
    """按名执行 MCP 工具：角色门控 → 参数校验 → handler(db, user, **args)。"""
    tool = get_mcp_tool(name)
    if tool.requires and getattr(user, "role", None) not in tool.requires:
        raise MCPToolForbidden(f"角色 {getattr(user, 'role', None)} 无权调用工具 {name}")
    args = tool.args_schema.model_validate(arguments)
    return await tool.handler(db=db, user=user, **args.model_dump())
