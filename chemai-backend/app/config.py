"""全局配置。

技术选型依据见设计文档 38-技术选型与工具链：
- Web 框架：FastAPI + Uvicorn
- ORM/迁移：SQLAlchemy 2.0 + Alembic
- 数据库：SQLite + WAL（开发），MySQL 可选（生产）
- 向量库：ChromaDB（Embedding: DashScope text-embedding-v3, 1024 维）
- LLM：三级 Fallback 路由（首选 MiMo-V2.5 → 通义千问 qwen-turbo → DeepSeek-V4-Flash）
- Agent：LangGraph create_react_agent（单 Agent v2，v1 多 Agent 保留为回退）
- OCR：百度教育 OCR 主力 / MinerU PDF / VLM 兜底
"""
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings:
    app_name: str = "ChemAI"

    # 三个数据库文件（见设计文档 38-五）：主库 / Agent 检查点 / Agent 长期记忆
    main_db: str = str(BASE_DIR / "data" / "chemai.db")
    agent_checkpoint_db: str = str(BASE_DIR / "data" / "agent_checkpoints.db")
    agent_memory_db: str = str(BASE_DIR / "data" / "agent_memory.db")

    # LLM Provider（.env 中覆盖）
    llm_provider: str = "deepseek"   # deepseek / qwen / mimo
    llm_api_key: str = ""

    # OCR Provider
    ocr_provider: str = "baidu"      # baidu / mineru
    baidu_ocr_api_key: str = ""
    baidu_ocr_secret_key: str = ""


settings = Settings()
