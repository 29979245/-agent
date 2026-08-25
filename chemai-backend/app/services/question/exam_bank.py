"""题库管理服务（doc 47 §5，设计 §5）：QuestionSet CRUD + 题目关联 + 真题复制入库。

删除语义（D8）：删除题库文件夹仅级联删 QuestionSetItem 关联，题目实体保留；
系统预设文件夹（is_preset）禁删。
"""
from __future__ import annotations

from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.db.models import Question, QuestionSet, QuestionSetItem
from app.db.models.enums import AuditStatus, Difficulty, QuestionSource
from app.services.question.historical import HistoricalBank
from app.services.question.serializers import PAGE_SIZE, paginate, question_dict

# 真题复制入库的难度映射：未知难度统一回落 medium
_DIFFICULTY_VALUES = {d.value for d in Difficulty}


def _set_summary(qs: QuestionSet) -> dict:
    return {
        "id": qs.id,
        "name": qs.name,
        "teacher_id": qs.teacher_id,
        "region": qs.region,
        "year": qs.year,
        "description": qs.description,
        "question_count": qs.question_count,
        "is_preset": qs.is_preset,
        "created_at": qs.created_at.isoformat() if qs.created_at else None,
    }


class ExamBankService:
    """题库管理服务：QuestionSet 两级结构的业务操作。"""

    def __init__(self, db: Session):
        self.db = db

    # ---- QuestionSet CRUD ----

    def create_set(
        self,
        name: str,
        teacher_id: int = 0,
        region: str = "",
        year: int = 0,
        description: str = "",
    ) -> QuestionSet:
        qs = QuestionSet(
            name=name,
            teacher_id=teacher_id,
            region=region,
            year=year,
            description=description,
            question_count=0,
            is_preset=False,
        )
        self.db.add(qs)
        self.db.flush()
        return qs

    def list_sets(
        self,
        teacher_id: Optional[int] = None,
        region: Optional[str] = None,
        year: Optional[int] = None,
        page: int = 1,
        page_size: int = PAGE_SIZE,
    ) -> dict:
        query = self.db.query(QuestionSet)
        if teacher_id:
            # 预设（teacher_id=0）恒包含，与当前教师自己的并集展示
            query = query.filter(QuestionSet.teacher_id.in_([0, teacher_id]))
        if region:
            query = query.filter(QuestionSet.region == region)
        if year:
            query = query.filter(QuestionSet.year == year)
        rows, total, page = paginate(
            query, page, page_size, (QuestionSet.is_preset.desc(), QuestionSet.id.desc())
        )
        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "items": [_set_summary(s) for s in rows],
        }

    def get_set(self, set_id: int) -> QuestionSet:
        qs = self.db.get(QuestionSet, set_id)
        if qs is None:
            raise NotFoundError()
        return qs

    def get_set_questions(self, set_id: int) -> list[dict]:
        """详情：返回文件夹全部关联题目。"""
        qs = self.get_set(set_id)
        return [
            question_dict(item.question)
            for item in qs.items
            if item.question is not None
        ]

    def delete_set(self, set_id: int) -> None:
        qs = self.get_set(set_id)
        if qs.is_preset:
            raise HTTPException(status_code=400, detail="系统预设题库不可删除")
        # 级联删 QuestionSetItem 关联（ORM cascade="all, delete-orphan"），题目实体保留
        self.db.delete(qs)
        self.db.flush()

    # ---- 题目关联 ----

    def import_questions(self, set_id: int, question_ids: list[int]) -> dict:
        """批量入库，返回逐题跳过原因 skipped_reasons（与 add_questions 对齐）。"""
        qs = self.get_set(set_id)
        added, skipped = 0, 0
        skipped_reasons: dict[str, str] = {}
        for qid in question_ids:
            key = str(qid)
            existing = self.db.query(QuestionSetItem).filter_by(
                set_id=set_id, question_id=qid
            ).first()
            if existing is not None:
                skipped += 1
                skipped_reasons[key] = "已在文件夹中"
                continue
            question = self.db.get(Question, qid)
            if question is None:
                skipped += 1
                skipped_reasons[key] = "题目不存在"
                continue
            # blocked 不可入库（与 approve 一致）
            if question.audit_status == AuditStatus.blocked:
                skipped += 1
                skipped_reasons[key] = "审核未通过（blocked 不可入库）"
                continue
            self.db.add(
                QuestionSetItem(set_id=set_id, question_id=qid, sort_order=qs.question_count + added)
            )
            added += 1
        qs.question_count += added
        self.db.flush()
        return {"added": added, "skipped": skipped, "skipped_reasons": skipped_reasons}

    def remove_question(self, set_id: int, question_id: int) -> None:
        item = self.db.query(QuestionSetItem).filter_by(
            set_id=set_id, question_id=question_id
        ).first()
        if item is None:
            raise NotFoundError(detail="题库中未找到该题目")
        self.db.delete(item)
        qs = self.db.get(QuestionSet, set_id)
        if qs is not None:
            qs.question_count = max(0, qs.question_count - 1)
        self.db.flush()

    # ---- 渠道二：真题复制入库 ----

    def import_historical(self, bank: HistoricalBank, ref_id: str) -> Question:
        """从真题库复制构造 Question 实体写入题库（渠道二前置）。"""
        hq = bank.get_question(ref_id)
        if hq is None:
            raise NotFoundError(detail="真题库中未找到该题目")
        difficulty = hq.difficulty if hq.difficulty in _DIFFICULTY_VALUES else "medium"
        q = Question(
            content=hq.content,
            options=hq.options,
            answer=hq.answer,
            analysis=hq.analysis,
            knowledge_points=", ".join(hq.knowledge_points),
            difficulty=Difficulty(difficulty),
            source=QuestionSource.manual,  # 真题复制入库视为手动录入
            audit_status=AuditStatus.passed,
            audit_report={},
        )
        self.db.add(q)
        self.db.flush()
        return q
