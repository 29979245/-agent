"""Question ↔ API dict 共享序列化与分页默认值（exam_bank / exam_service / vector 共用）。"""
from __future__ import annotations

from sqlalchemy.orm import Query

from app.db.models import Question

PAGE_SIZE = 20


def paginate(
    query: Query, page: int, page_size: int, order_by=None
) -> tuple[list, int, int]:
    """SQLAlchemy Query 分页：返回 (本页行, 总数, 归一化页码)。

    order_by 可传单列或列元组（多列排序），原样交给 Query.order_by。
    """
    if order_by is not None:
        if isinstance(order_by, (tuple, list)):
            query = query.order_by(*order_by)
        else:
            query = query.order_by(order_by)
    total = query.count()
    page = max(page, 1)
    rows = query.offset((page - 1) * page_size).limit(page_size).all()
    return rows, total, page


def paginate_items(items: list, page: int, page_size: int) -> tuple[list, int, int]:
    """内存列表分页：返回 (本页切片, 总数, 归一化页码)。"""
    total = len(items)
    page = max(page, 1)
    start = (page - 1) * page_size
    return items[start : start + page_size], total, page


def split_knowledge_points(raw: str | None) -> list[str]:
    """String(500) 列 ↔ API list[str] 转换（设计 D4）。"""
    return [kp.strip() for kp in (raw or "").split(",") if kp.strip()]


def question_dict(q: Question) -> dict:
    return {
        "question_id": q.id,
        "content": q.content,
        "options": q.options or [],
        "answer": q.answer,
        "analysis": q.analysis or "",
        "knowledge_points": split_knowledge_points(q.knowledge_points),
        "difficulty": q.difficulty.value if q.difficulty else "medium",
        "source": q.source.value if q.source else "manual",
        "audit_status": q.audit_status.value if q.audit_status else "passed",
    }
