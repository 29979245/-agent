"""ChemAI 智辅化学 · FastAPI 应用入口。

启动命令（开发）：
    uvicorn app.main:app --reload --port 8000
"""
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.v1.auth import auth_router, parent_router
from app.config import settings
from app.core.exceptions import APIException

app = FastAPI(
    title="ChemAI 智辅化学 API",
    version="0.1.0",
    description="面向中学化学教学的 AI Agent 系统后端。",
)


@app.exception_handler(APIException)
def _api_exception_handler(request: Request, exc: APIException) -> JSONResponse:
    # 统一错误体：detail/error_code/suggestion（9.2）
    return JSONResponse(
        status_code=exc.status_code,
        content=exc.detail,
    )


@app.exception_handler(RequestValidationError)
def _validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    first = exc.errors()[0] if exc.errors() else {}
    return JSONResponse(
        status_code=422,
        content={
            "detail": f"请求参数校验失败: {first.get('loc', '')} {first.get('msg', '')}",
            "error_code": "VALIDATION_ERROR",
            "suggestion": "请检查请求参数格式",
        },
    )


app.include_router(auth_router, prefix="/api/auth", tags=["auth"])
app.include_router(parent_router, prefix="/api/parent", tags=["parent"])


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "app": settings.app_name}
