"""SQLAlchemy 声明基类，全部模型继承自此。"""
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
