"""自适应练习出题服务（doc 28 §5，design.md D1/D2/D7）。

按学生 ZPD 难度 + 薄弱知识点 + 主导障碍确定目标，从真题库确定性抽样复制为练习题，
为每位学生创建一份独立练习记录（per-student ExamRecord，ADR-0002）。批次硬限制 5 人。
"""
from __future__ import annotations

import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.db.models import ExamRecord, ExamStatus, Question, QuestionSource, Student
from app.db.models.enums import AuditStatus, Difficulty, ExamType
from app.services.exercise.sampling import sample_questions
from app.services.exercise.zpd import (
    adjust_difficulty,
    compute_zpd_difficulty,
    dominant_barrier,
    extract_weak_kps,
    fallback_kps_for,
)
from app.services.question.historical import HistoricalBank, HistoricalQuestion, get_bank

BATCH_LIMIT = 5


def copy_historical_question(
    db: Session, hq: HistoricalQuestion, exam_id: int, target_difficulty: str
) -> Question:
    """从真题库复制构造练习题（source=practice）：难度保留原题、空档回落目标档。"""
    raw_diff = hq.difficulty if hq.difficulty in {d.value for d in Difficulty} else ""
    return Question(
        content=hq.content,
        options=hq.options,
        answer=hq.answer,
        analysis=hq.analysis,
        knowledge_points=", ".join(hq.knowledge_points),
        difficulty=Difficulty(raw_diff or target_difficulty),
        source=QuestionSource.practice,
        audit_status=AuditStatus.passed,
        audit_report={},
        record_id=exam_id,
    )


class AdaptivePracticeService:
    """个性化练习生成：确定目标 → 抽样 → 建练习记录（每题一份独立卷）。"""

    def __init__(self, db: Session, bank: HistoricalBank | None = None):
        self.db = db
        self.bank = bank or get_bank()

    # ---- 目标解析 ----

    def plan_for(self, student: Student) -> dict:
        """解析出题目标：zpd 难度 / 目标难度（策略矩阵）/ 知识点列表。"""
        zpd = compute_zpd_difficulty(self.db, student.id)
        barrier = dominant_barrier(student)
        weak = extract_weak_kps(self.db, student.id, top_n=3)
        kps = weak or fallback_kps_for(barrier)
        difficulty = adjust_difficulty(zpd, barrier)
        return {
            "zpd_difficulty": zpd,
            "difficulty": difficulty,
            "barrier": barrier,
            "knowledge_points": kps,
            "weak_kps": weak,
        }

    # ---- 单学生出题 ----

    def generate_for_student(
        self,
        student: Student,
        count: int = 3,
        name: str = "自适应练习",
        exclude_ref_ids: tuple[str, ...] = (),
    ) -> dict:
        """为单个学生生成一份个性化练习（per-student ExamRecord + 复制的练习题）。"""
        plan = self.plan_for(student)
        selected, shortfall = sample_questions(
            self.bank, plan["knowledge_points"], plan["difficulty"],
            count=count, exclude_ref_ids=exclude_ref_ids,
        )
        exam = ExamRecord(
            class_id=None,
            student_id=student.id,
            name=name,
            exam_type=ExamType.practice,
            status=ExamStatus.published,
            exam_date=datetime.date.today(),
            question_stats={
                "difficulty": plan["difficulty"],
                "zpd_difficulty": plan["zpd_difficulty"],
                "barrier": plan["barrier"],
                "deadline": None,
            },
        )
        self.db.add(exam)
        self.db.flush()
        for ref, hq in selected:
            self.db.add(copy_historical_question(self.db, hq, exam.id, plan["difficulty"]))
        self.db.flush()
        return {
            "student_id": student.id,
            "exam_id": exam.id,
            "practice_id": exam.id,
            "question_count": len(selected),
            "shortfall": shortfall,
            "difficulty": plan["difficulty"],
            "knowledge_points": plan["knowledge_points"],
            "title": exam.name,
        }

    # ---- 批量出题（硬限制 5 人） ----

    def generate_batch(
        self,
        student_ids: list[int],
        count: int = 3,
        name: str = "自适应练习",
        exclude_ref_ids: tuple[str, ...] = (),
    ) -> dict:
        """批量出题：每批最多 5 人，超过提示剩余数量，由调用方分批触发。"""
        remaining = max(0, len(student_ids) - BATCH_LIMIT)
        results: list[dict] = []
        for sid in student_ids[:BATCH_LIMIT]:
            student = self.db.get(Student, sid)
            if student is None:
                continue
            results.append(self.generate_for_student(
                student, count=count, name=name, exclude_ref_ids=exclude_ref_ids,
            ))
        return {
            "results": results,
            "batch_limit": BATCH_LIMIT,
            "remaining": remaining,
        }
