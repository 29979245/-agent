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
import os
from pathlib import Path

from app.core.env import load_dotenv_file

BASE_DIR = Path(__file__).resolve().parent.parent


# 让 .env 中的键（如 JWT_SECRET / BAIDU_OCR_API_KEY / LLM_API_KEY）生效，对齐 .env.example 声明。
# app/__init__.py 已先行加载；此处兜底再调一次，直接 import app.config 的场景也可靠。
load_dotenv_file()


class Settings:
    app_name: str = "ChemAI"

    # 三个数据库文件（见设计文档 38-五）：主库 / Agent 检查点 / Agent 长期记忆
    main_db: str = str(BASE_DIR / "data" / "chemai.db")
    agent_checkpoint_db: str = str(BASE_DIR / "data" / "agent_checkpoints.db")
    agent_memory_db: str = str(BASE_DIR / "data" / "agent_memory.db")

    # 题库与向量库（Group 7.1）：真题库目录（地区/年份/试卷 三层 JSON）与 ChromaDB 持久化路径
    exam_bank_dir: str = str(BASE_DIR / "data" / "exam_bank")
    chroma_dir: str = str(BASE_DIR / "data" / "chroma")

    # OCR 上传文件落盘目录（批量上传保存待识别文件）
    ocr_upload_dir: str = str(BASE_DIR / "data" / "ocr_uploads")

    # 调度（design.md D8）：默认关闭，测试避免后台线程；生产 .env 中开启
    enable_scheduler: bool = os.environ.get("ENABLE_SCHEDULER", "false").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )

    # LLM Provider（.env 中覆盖；agent 阶段 LLM_PROVIDER 应设为 mimo，见 design D2/ADR-0008）
    llm_provider: str = os.environ.get("LLM_PROVIDER", "deepseek")  # deepseek / qwen / mimo
    llm_api_key: str = os.environ.get("LLM_API_KEY", "")

    # 三级 Fallback 各 Provider 独立密钥（ADR-0008：MiMo-V2.5 主 → qwen-turbo → DeepSeek）
    mimo_api_key: str = os.environ.get("MIMO_API_KEY", "")
    qwen_api_key: str = os.environ.get("QWEN_API_KEY", "")
    deepseek_api_key: str = os.environ.get("DEEPSEEK_API_KEY", "") or llm_api_key

    # 各 Provider 兼容接口 base_url（qwen 走 DashScope 兼容模式；MiMo 按部署配置 MIMO_BASE_URL）
    mimo_base_url: str = os.environ.get("MIMO_BASE_URL", "")
    qwen_base_url: str = os.environ.get(
        "QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    deepseek_base_url: str = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")

    # Agent 审计日志目录（doc 30 §10）：JSONL 追加写入 + 内存环形缓冲
    agent_audit_dir: str = str(BASE_DIR / "data" / "audit")

    # web_search 独立搜索 API（design D15：与 LLM Provider 解耦）
    search_api_base: str = os.environ.get("SEARCH_API_BASE", "")
    search_api_key: str = os.environ.get("SEARCH_API_KEY", "")

    # OCR Provider
    ocr_provider: str = os.environ.get("OCR_PROVIDER", "baidu")  # baidu / mineru
    baidu_ocr_api_key: str = os.environ.get("BAIDU_OCR_API_KEY", "")
    baidu_ocr_secret_key: str = os.environ.get("BAIDU_OCR_SECRET_KEY", "")

    # Agent 限流（design D6）：Token Bucket，按用户；参数从简可后调
    agent_rate_per_minute: int = int(os.environ.get("AGENT_RATE_PER_MINUTE", "12"))
    agent_rate_burst: int = int(os.environ.get("AGENT_RATE_BURST", "20"))


settings = Settings()
