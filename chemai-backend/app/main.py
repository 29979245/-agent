"""ChemAI 智辅化学 · FastAPI 应用入口。

启动命令（开发）：
    uvicorn app.main:app --reload --port 8000
"""
from fastapi import FastAPI

from app.config import settings

app = FastAPI(
    title="ChemAI 智辅化学 API",
    version="0.1.0",
    description="面向中学化学教学的 AI Agent 系统后端。",
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "app": settings.app_name}
