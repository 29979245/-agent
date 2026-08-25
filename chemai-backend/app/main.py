"""ChemAI 智辅化学 · FastAPI 应用入口。

启动命令（开发）：
    uvicorn app.main:app --reload --port 8000
"""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.v1.audit import audit_router
from app.api.v1.auth import auth_router, parent_router
from app.api.v1.exam import classes_router, exam_router
from app.api.v1.exam_bank import exam_bank_router
from app.config import settings
from app.core.exceptions import APIException
from app.core.middleware import AuthMiddleware
from app.services.question.historical import reload_bank
from app.services.question.vector import reload_vector

# 前端静态页目录：app/main.py → chemai-backend/frontend/pages
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend" / "pages"

logger = logging.getLogger(__name__)


def startup_services(exam_bank_dir: str | None = None, chroma_dir: str | None = None) -> dict:
    """启动加载：真题库入内存 + 向量索引初始化；返回统计供日志/测试断言。"""
    bank = reload_bank(exam_bank_dir or settings.exam_bank_dir)
    reload_vector(chroma_dir or settings.chroma_dir)
    logger.info(
        "[ExamBank] 从 %d 个文件加载了 %d 道真题",
        bank.loaded_files,
        bank.loaded_questions,
    )
    return {"loaded_files": bank.loaded_files, "loaded_questions": bank.loaded_questions}


@asynccontextmanager
async def lifespan(_: FastAPI):
    startup_services()
    yield


app = FastAPI(
    title="ChemAI 智辅化学 API",
    version="0.1.0",
    description="面向中学化学教学的 AI Agent 系统后端。",
    lifespan=lifespan,
)


@app.exception_handler(APIException)
def _api_exception_handler(request: Request, exc: APIException) -> JSONResponse:
    # 统一错误体：detail/error_code/suggestion（9.2）
    return JSONResponse(
        status_code=exc.status_code,
        content=exc.detail,
    )


@app.exception_handler(404)
def _not_found_handler(request: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={"detail": "资源不存在", "error_code": "NOT_FOUND", "suggestion": ""},
    )


@app.exception_handler(HTTPException)
def _http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": str(exc.detail)})


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


app.add_middleware(AuthMiddleware)

# 前端开发跨源（桌面端本地调试 / 未来独立部署）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router, prefix="/api/auth", tags=["auth"])
app.include_router(parent_router, prefix="/api/parent", tags=["parent"])
app.include_router(audit_router, prefix="/api/question", tags=["question"])
app.include_router(exam_bank_router, prefix="/api/exam-bank", tags=["exam-bank"])
app.include_router(exam_router, prefix="/api/exam", tags=["exam"])
app.include_router(classes_router, prefix="/api", tags=["org"])

# 静态页托管：/pages/login.html、/pages/exam-v2.html
app.mount("/pages", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="pages")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "app": settings.app_name}
