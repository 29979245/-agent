"""MCP 工具服务器端点（doc 30 十二 / spec「MCP 工具服务器」，挂载于 /api/mcp）。

- GET  /api/mcp/tools            列出全部 16 个工具及其参数 Schema
- POST /api/mcp/call             通用调用（body 含 tool + arguments）
- POST /api/mcp/tools/{name}     按名称直接调用（body 仅 arguments）

认证由 AuthMiddleware 执行（/api/mcp 不在白名单 → 未携带令牌 401）。
未注册工具名 → 404 MCP_TOOL_NOT_FOUND；角色越权 → 403 MCP_TOOL_FORBIDDEN。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.orm import Session

from app.agents.mcp import tools as _tools  # noqa: F401  # 导入触发 16 工具装饰器注册
from app.agents.mcp.registry import (
    MCPToolForbidden,
    MCPToolNotFound,
    call_mcp_tool,
    list_mcp_tools,
)
from app.core.exceptions import APIException
from app.core.permissions import UserContext
from app.db.session import get_db

mcp_router = APIRouter()


class McpCallRequest(BaseModel):
    tool: str = Field(..., description="MCP 工具名")
    arguments: dict = Field(default_factory=dict, description="工具参数")


class McpArgsRequest(BaseModel):
    arguments: dict = Field(default_factory=dict, description="工具参数")


def _user(request: Request) -> UserContext:
    payload = request.state.user
    return UserContext(
        user_id=payload["user_id"],
        role=payload["role"],
        school_id=payload.get("school_id"),
    )


def _raise_mcp_error(exc: Exception) -> None:
    """MCP 工具调用异常 → 统一错误（404 未注册 / 403 越权 / 422 参数非法）；其余异常上抛。"""
    if isinstance(exc, MCPToolNotFound):
        raise APIException(404, f"未注册的 MCP 工具: {exc}", "MCP_TOOL_NOT_FOUND",
                           suggestion="请先 GET /api/mcp/tools 查看可用工具") from exc
    if isinstance(exc, MCPToolForbidden):
        raise APIException(403, str(exc), "MCP_TOOL_FORBIDDEN",
                           suggestion="当前角色无权调用该工具") from exc
    if isinstance(exc, ValidationError):
        raise APIException(422, f"MCP 工具参数校验失败: {exc}", "MCP_ARGUMENTS_INVALID",
                           suggestion="请按工具的 parameters Schema 传入参数") from exc


@mcp_router.get("/tools")
async def mcp_tools() -> dict:
    tools = list_mcp_tools()
    return {"count": len(tools), "tools": tools}


@mcp_router.post("/call")
async def mcp_call(request: Request, body: McpCallRequest, db: Session = Depends(get_db)) -> dict:
    try:
        result = await call_mcp_tool(body.tool, db=db, user=_user(request), arguments=body.arguments)
    except (MCPToolNotFound, MCPToolForbidden, ValidationError) as exc:
        _raise_mcp_error(exc)
    return {"tool": body.tool, "result": result}


@mcp_router.post("/tools/{name}")
async def mcp_tools_name(request: Request, name: str, body: McpArgsRequest, db: Session = Depends(get_db)) -> dict:
    try:
        result = await call_mcp_tool(name, db=db, user=_user(request), arguments=body.arguments)
    except (MCPToolNotFound, MCPToolForbidden, ValidationError) as exc:
        _raise_mcp_error(exc)
    return {"tool": name, "result": result}
