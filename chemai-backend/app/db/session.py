"""数据库引擎与会话工厂。

SQLite 三库之一的主库引擎：启用 JSON 序列化往返、外键约束与 WAL 模式。
（设计文档 38：SQLite + WAL 开发，MySQL 生产仅改连接串。）
"""
import json

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.config import settings


def make_engine(db_path: str = settings.main_db):
    engine = create_engine(
        f"sqlite:///{db_path}",
        json_serializer=lambda obj: json.dumps(obj, ensure_ascii=False),
        json_deserializer=lambda text: json.loads(text),
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    return engine


engine = make_engine()
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def get_db():
    """FastAPI 依赖：每请求一个会话，结束自动关闭。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
