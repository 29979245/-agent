"""错题强化训练服务（doc 29 §5-§8，design.md D1/D2）。

- 错题列表：全部错误作答按题目去重，最近作答倒序，累计答错次数；已掌握（ReviewTask done）移除
- 变式题：同知识点同难度题库抽样复制（source=practice），排除原题内容相同的题，抽样不足标记
- 训练会话：per-student ExamRecord 训练记录，引用原题题目 id（不复制，保持错题/复习任务同一 identity）
- 标记已掌握：校验错题归属 → ReviewTask 置 done（终态），非本人错题 403
"""
from __future__ import annotations

import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.core.exceptions import ForbiddenError, NotFoundError
from app.db.models import ExamRecord, ExamStatus, ExamType, Question, ReviewTask, StudentAnswer
from app.db.models.enums import Difficulty, QuestionSource, ReviewLevel, ReviewTaskStatus
from app.services.exercise.adaptive import copy_historical_question
from app.services.exercise.sampling import sample_questions
from app.services.question.historical import HistoricalBank, get_bank
from app.services.question.serializers import question_dict, split_knowledge_points

TRAINING_NAME = "错题训练"
VARIANT_NAME = "错题变式"


class WrongQuestionTrainer:
    """错题列表 / 变式生成 / 训练会话 / 标记已掌握。"""

    def __init__(self, db: Session, bank: HistoricalBank | None = None):
        self.db = db
        self.bank = bank or get_bank()

    # ---- 4.1 错题列表 ----

    def list_wrong_questions(self, student_id: int) -> list[dict]:
        """按最近作答倒序去重返回错题，含累计答错次数；已掌握（ReviewTask done）移除。"""
        rows = (
            self.db.query(StudentAnswer)
            .filter(
                StudentAnswer.student_id == student_id,
                StudentAnswer.is_correct.is_(False),
            )
            .order_by(StudentAnswer.answered_at.desc().nullsfirst(), StudentAnswer.id.desc())
            .all()
        )
        mastered = {
            task.question_id
            for task in self.db.query(ReviewTask).filter(
                ReviewTask.student_id == student_id,
                ReviewTask.status == ReviewTaskStatus.done,
            )
        }
        errors: dict[int, list[StudentAnswer]] = {}
        for ans in rows:
            errors.setdefault(ans.question_id, []).append(ans)
        result: list[dict] = []
        for qid, ans_rows in errors.items():
            if qid in mastered:
                continue
            question = self.db.get(Question, qid)
            if question is None:
                continue
            latest = ans_rows[0]
            result.append({
                "question_id": qid,
                "content": question.content,
                "options": question.options or [],
                "knowledge_points": split_knowledge_points(question.knowledge_points),
                "difficulty": question.difficulty.value if question.difficulty else "medium",
                "error_count": len(ans_rows),
                "last_answered_at": latest.answered_at.isoformat() if latest.answered_at else None,
            })
        return result

    # ---- 4.2 变式题生成 ----

    def generate_variants(
        self, student_id: int, question_id: int, count: int = 1
    ) -> dict:
        """同知识点同难度题库抽样复制为变式题，排除原题，抽样不足标记不足。"""
        original = self.db.get(Question, question_id)
        if original is None:
            raise NotFoundError(detail="题目不存在", error_code="QUESTION_NOT_FOUND")
        kps = split_knowledge_points(original.knowledge_points)
        diff = original.difficulty.value if original.difficulty else "medium"
        exclude = [
            f"{paper.region}/{paper.year}/{paper.name}#{hq.id}"
            for paper in self.bank.papers
            for hq in paper.questions
            if hq.content == original.content
        ]
        selected, shortfall = sample_questions(
            self.bank, kps, diff, count=count, exclude_ref_ids=tuple(exclude)
        )
        exam = ExamRecord(
            class_id=None,
            student_id=student_id,
            name=VARIANT_NAME,
            exam_type=ExamType.practice,
            status=ExamStatus.published,
            exam_date=datetime.date.today(),
            question_stats={"mode": "variant", "difficulty": diff, "deadline": None},
        )
        self.db.add(exam)
        self.db.flush()
        copied: list[Question] = []
        for ref, hq in selected:
            copied.append(copy_historical_question(self.db, hq, exam.id, diff))
        self.db.add_all(copied)
        self.db.flush()
        return {
            "student_id": student_id,
            "exam_id": exam.id,
            "question_count": len(copied),
            "shortfall": shortfall,
            "difficulty": diff,
            "questions": [question_dict(q, include_answer=False) for q in copied],
        }

    # ---- 4.3 训练会话 ----

    def start_training(self, student_id: int, question_ids: list[int]) -> dict:
        """创建 per-student 训练记录，引用原题题目（不复制）。"""
        from sqlalchemy import select
        existing = set(self.db.execute(select(Question.id).where(Question.id.in_(question_ids))).scalars().all())
        missing = [qid for qid in question_ids if qid not in existing]
        if missing:
            raise NotFoundError(detail=f"题目不存在：{missing}", error_code="QUESTION_NOT_FOUND")
        exam = ExamRecord(
            class_id=None,
            student_id=student_id,
            name=TRAINING_NAME,
            exam_type=ExamType.practice,
            status=ExamStatus.published,
            exam_date=datetime.date.today(),
            question_stats={
                "mode": "training",
                "question_ids": list(question_ids),
                "deadline": None,
            },
        )
        self.db.add(exam)
        self.db.flush()
        return {
            "student_id": student_id,
            "exam_id": exam.id,
            "question_count": len(question_ids),
            "question_ids": list(question_ids),
        }

    # ---- 4.4 标记已掌握 ----

    def mark_mastered(self, student_id: int, question_id: int, now: Optional[datetime.datetime] = None) -> dict:
        """校验错题归属后置 ReviewTask done（终态），从错题列表移除。"""
        now = now or datetime.datetime.utcnow()
        owned = (
            self.db.query(StudentAnswer.id)
            .filter(
                StudentAnswer.student_id == student_id,
                StudentAnswer.question_id == question_id,
                StudentAnswer.is_correct.is_(False),
            )
            .first()
        )
        if owned is None:
            raise ForbiddenError(detail="非本人错题，无法标记已掌握", error_code="WRONG_QUESTION_NOT_OWNED")
        task = (
            self.db.query(ReviewTask)
            .filter(ReviewTask.student_id == student_id, ReviewTask.question_id == question_id)
            .first()
        )
        if task is None:
            task = ReviewTask(
                student_id=student_id,
                question_id=question_id,
                review_level=ReviewLevel.level1,
                status=ReviewTaskStatus.pending,
                next_review_at=now,
                first_studied_at=now,
            )
            self.db.add(task)
            self.db.flush()
        task.review_level = ReviewLevel.level6
        task.status = ReviewTaskStatus.done
        task.completed_at = now
        task.next_review_at = None
        self.db.flush()
        return {
            "review_task_id": task.id,
            "question_id": question_id,
            "status": ReviewTaskStatus.done.value,
            "review_level": ReviewLevel.level6.value,
        }
