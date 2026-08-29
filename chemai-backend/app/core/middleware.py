"""全局认证中间件（9.1）：白名单放行 + Bearer 校验 + 401 拒绝。

沿设计文档 23 §7.1 的白名单前缀；拒绝时记结构化日志（D11），
响应体走统一错误码格式（9.2）。
"""
import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.core.security import InvalidTokenError, TokenExpiredError, decode_token

guard_logger = logging.getLogger("chemai.guard")

WHITELIST_PREFIXES = (
    "/api/auth/",
    "/api/parent/",
    "/api/ocr/",
    "/api/classes/",
    "/api/knowledge/",
    "/api/exam-bank/",
    "/api/question/",
    "/pages",  # 前端静态页（登录页/工作台）无需鉴权
    "/docs",
    "/openapi.json",
    "/health",
)


def _error_body(detail: str, error_code: str, suggestion: str) -> dict:
    return {"detail": detail, "error_code": error_code, "suggestion": suggestion}


class AuthMiddleware(BaseHTTPMiddleware):
    """对非白名单 /api 请求执行 JWT 校验，通过后写入 request.state.user。"""

    def __init__(self, app, whitelist: tuple[str, ...] = WHITELIST_PREFIXES):
        super().__init__(app)
        self.whitelist = whitelist

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if any(path.startswith(prefix) for prefix in self.whitelist):
            return await call_next(request)

        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            guard_logger.info(
                "auth_middleware_reject",
                extra={
                    "event": "auth_reject",
                    "reason": "missing_token",
                    "path": path,
                },
            )
            return JSONResponse(
                status_code=401,
                content=_error_body(
                    "缺少认证令牌", "AUTHENTICATION_REQUIRED", "请在请求头携带 Bearer token"
                ),
            )

        token = auth[len("Bearer "):]
        try:
            payload = decode_token(token, expected_type="access")
        except TokenExpiredError:
            guard_logger.info(
                "auth_middleware_reject",
                extra={"event": "auth_reject", "reason": "token_expired", "path": path},
            )
            return JSONResponse(
                status_code=401,
                content=_error_body("令牌已过期", "TOKEN_EXPIRED", "请使用 refresh token 刷新或重新登录"),
            )
        except InvalidTokenError:
            guard_logger.info(
                "auth_middleware_reject",
                extra={"event": "auth_reject", "reason": "token_invalid", "path": path},
            )
            return JSONResponse(
                status_code=401,
                content=_error_body("无效令牌", "TOKEN_INVALID", "请重新登录获取有效令牌"),
            )

        request.state.user = payload
        return await call_next(request)
