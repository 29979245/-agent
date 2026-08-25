"""考试生命周期服务（doc 47 §6，设计 §6）：六态流转 + 双渠道题目关联 + 发布/完成统计。

删除语义（D8）：draft 可级联删除（作答经 exam_id CASCADE 删除，关联题 record_id
SET NULL 置空、题目实体保留）；发布后删除受限。渠道二从真题库复制 Question 实体
写入题库后关联（question.source=manual）。
"""
from __future__ import annotations

import datetime
from collections import Counter

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.db.models import ExamRecord, Question, Student, StudentAnswer
from app.db.models.enums import ExamStatus, ExamType
from app.services.question.exam_state import ExamStateError, is_deletable, transition
from app.services.question.historical import HistoricalBank, get_bank
from app.services.question.serializers import paginate, question_dict


class ExamService:
    """考试从创建到归档的业务操作；state 转换一律经六态状态机守卫。"""

    def __init__(self, db: Session, bank: HistoricalBank | None = None):
        self.db = db
        self.bank = bank or get_bank()

    def _get(self, exam_id: int) -> ExamRecord:
        exam = self.db.get(ExamRecord, exam_id)
        if exam is None:
            raise NotFoundError()
        return exam

    def _move(self, exam: ExamRecord, target: ExamStatus) -> None:
        """状态机转换；非法转换转 400。"""
        try:
            exam.status = transition(exam.status, target)
        except ExamStateError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    def _require_draft(self, exam: ExamRecord) -> None:
        if exam.status != ExamStatus.draft:
            raise HTTPException(status_code=400, detail=f"仅草稿状态可操作，当前 {exam.status.value}")

    # ---- 创建 ----

    def create(
        self,
        class_id: int,
        name: str = "",
        exam_date: datetime.date = datetime.date.today(),
        exam_type: str = "exam",
    ) -> ExamRecord:
        exam = ExamRecord(
            class_id=class_id,
            name=name,
            exam_type=ExamType(exam_type),
            exam_date=exam_date,
            status=ExamStatus.draft,
            attendee_count=0,
            stats={},
            question_stats={},
        )
        self.db.add(exam)
        self.db.flush()
        return exam

    # ---- 4.3 题目双渠道关联 ----

    def add_questions(self, exam_id: int, question_ids: list[int | str]) -> dict:
        """批量关联：int 走渠道一（Question 表直接关联）；str 走渠道二（真题复制入库）。

        返回逐题跳过原因 skipped_reasons（question_id → 原因），供前端向教师解释。
        """
        exam = self._get(exam_id)
        self._require_draft(exam)
        added, skipped = 0, 0
        skipped_reasons: dict[str, str] = {}
        seen_refs: set[str] = set()
        for qid in question_ids:
            if isinstance(qid, str):
                # 渠道二去重：同批已复制 → 跳过；该真题内容已复制并关联本考试 → 跳过
                if qid in seen_refs:
                    skipped += 1
                    skipped_reasons[qid] = "同批重复，已复制过"
                    continue
                hq = self.bank.get_question(qid)
                if hq is None:
                    skipped += 1
                    skipped_reasons[qid] = "真题未命中"
                    continue
                if self.db.query(Question).filter(
                    Question.record_id == exam_id,
                    Question.content == hq.content,
                ).first() is not None:
                    skipped += 1
                    skipped_reasons[qid] = "该真题已在本考试中"
                    continue
                q = self._import_historical(qid)
                if q is None:
                    skipped += 1
                    skipped_reasons[qid] = "真题复制失败"
                    continue
                seen_refs.add(qid)
            else:
                key = str(qid)
                q = self.db.get(Question, qid)
                if q is None:
                    skipped += 1
                    skipped_reasons[key] = "题目不存在"
                    continue
                if q.record_id is not None and q.record_id != exam_id:
                    other = self.db.get(ExamRecord, q.record_id)
                    other_name = other.name if other else f"考试#{q.record_id}"
                    skipped += 1
                    skipped_reasons[key] = f"已被考试「{other_name}」占用"
                    continue
                if q.record_id == exam_id:
                    skipped += 1
                    skipped_reasons[key] = "已在本考试中"
                    continue
            q.record_id = exam_id
            added += 1
        self.db.flush()
        return {"added": added, "skipped": skipped, "skipped_reasons": skipped_reasons}

    def _import_historical(self, ref_id: str) -> Question | None:
        """渠道二：复用题库服务复制构造 Question 实体写入题库；未命中返回 None。"""
        from app.services.question.exam_bank import ExamBankService

        try:
            q = ExamBankService(self.db).import_historical(self.bank, ref_id)
        except NotFoundError:
            return None
        from app.services.question.vector import sync_question_to_vector

        sync_question_to_vector(q)  # 5.4 保存入库后增量 append 同步
        return q

    def list_questions(self, exam_id: int, include_answer: bool = True) -> list[dict]:
        self._get(exam_id)
        rows = self.db.query(Question).filter_by(record_id=exam_id).all()
        return [question_dict(q, include_answer=include_answer) for q in rows]

    def remove_question(self, exam_id: int, question_id: int) -> None:
        exam = self._get(exam_id)
        self._require_draft(exam)
        q = self.db.get(Question, question_id)
        if q is None or q.record_id != exam_id:
            raise NotFoundError(detail="考试中未找到该题目")
        q.record_id = None
        self.db.flush()

    # ---- 状态流转 ----

    def publish(self, exam_id: int) -> ExamRecord:
        """Draft→Published：≥1 题校验 + 写 question_stats（发布元数据）。"""
        exam = self._get(exam_id)
        question_count = self.db.query(Question).filter_by(record_id=exam_id).count()
        if question_count < 1:
            raise HTTPException(status_code=400, detail="考试至少需要一道题目才能发布")
        exam.question_stats = {
            "published": True,
            "published_at": datetime.datetime.utcnow().isoformat(),
            "question_count": question_count,
            "total_students": self.db.query(Student).filter_by(class_id=exam.class_id).count(),
        }
        self._move(exam, ExamStatus.published)
        self.db.flush()
        return exam

    def start_grading(self, exam_id: int) -> ExamRecord:
        """教师触发阅卷：in_progress→Grading；若仍为 Published 先经首生作答进 InProgress。"""
        exam = self._get(exam_id)
        if exam.status == ExamStatus.published:
            self._move(exam, ExamStatus.in_progress)
        self._move(exam, ExamStatus.grading)
        self.db.flush()
        return exam

    def finalize(self, exam_id: int) -> ExamRecord:
        """Grading→Completed：统计参考人数（attendee_count）与班级统计（stats）。"""
        exam = self._get(exam_id)
        self._move(exam, ExamStatus.completed)
        answers = self.db.query(StudentAnswer).filter_by(exam_id=exam_id).all()
        attended = {a.student_id for a in answers}
        wrong = Counter(a.question_id for a in answers if not a.is_correct)
        exam.attendee_count = len(attended)
        exam.stats = {
            "total_students": self.db.query(Student).filter_by(class_id=exam.class_id).count(),
            "attended": len(attended),
            "question_count": self.db.query(Question).filter_by(record_id=exam_id).count(),
            "wrong_counts": dict(wrong),
        }
        self.db.flush()
        return exam

    def archive(self, exam_id: int) -> ExamRecord:
        """Completed→Archived（终态只读）。"""
        exam = self._get(exam_id)
        self._move(exam, ExamStatus.archived)
        self.db.flush()
        return exam

    def delete(self, exam_id: int) -> None:
        """级联删除：仅 Draft。作答经 FK CASCADE 删除，关联题 record_id SET NULL。"""
        exam = self._get(exam_id)
        if not is_deletable(exam.status):
            raise HTTPException(status_code=400, detail="仅草稿状态可删除考试")
        self.db.delete(exam)
        self.db.flush()

    # ---- 结果查询 ----

    def results(self, exam_id: int) -> dict:
        """全班成绩总览：学生 → 正确题数/总题数/正确率。"""
        exam = self._get(exam_id)
        questions = self.db.query(Question).filter_by(record_id=exam_id).all()
        qids = {q.id for q in questions}
        answers = self.db.query(StudentAnswer).filter_by(exam_id=exam_id).all()
        per_student: dict[int, list[StudentAnswer]] = {}
        for a in answers:
            per_student.setdefault(a.student_id, []).append(a)
        students = self.db.query(Student).filter(
            Student.id.in_(per_student.keys())
        ).all() if per_student else []
        student_rows = []
        for stu in students:
            stu_answers = per_student.get(stu.id, [])
            correct = sum(1 for a in stu_answers if a.is_correct)
            student_rows.append({
                "student_id": stu.id,
                "name": stu.name,
                "answered_count": len(stu_answers),
                "correct_count": correct,
                "accuracy": round(correct / len(stu_answers), 4) if stu_answers else 0,
            })
        avg_score = (
            round(sum(r["accuracy"] for r in student_rows) / len(student_rows), 4)
            if student_rows
            else 0
        )
        return {
            "exam_id": exam_id,
            "name": exam.name,
            "total_students": len(students),
            "question_count": len(qids),
            "avg_score": avg_score,
            "students": student_rows,
        }

    def student_result(self, exam_id: int, student_id: int) -> dict:
        """单学生逐题作答详情。"""
        self._get(exam_id)
        answers = (
            self.db.query(StudentAnswer)
            .filter_by(exam_id=exam_id, student_id=student_id)
            .order_by(StudentAnswer.question_id)
            .all()
        )
        items = []
        for a in answers:
            q = self.db.get(Question, a.question_id)
            items.append({
                "question_id": a.question_id,
                "content": q.content if q else "",
                "answer": q.answer if q else "",
                "student_answer": a.answer_text,
                "is_correct": a.is_correct,
                "barrier_type": a.barrier_type.value if a.barrier_type else None,
            })
        return {"exam_id": exam_id, "student_id": student_id, "answers": items}

    def list_exams(self, page: int = 1, page_size: int = 20) -> dict:
        """分页列出考试概要（id 倒序，含班级名/题目数/考试日期）。"""
        query = self.db.query(ExamRecord)
        rows, total, page = paginate(query, page, page_size, ExamRecord.id.desc())
        items = [
            {
                "exam_id": exam.id,
                "name": exam.name,
                "class_name": exam.class_.name if exam.class_ else "",
                "question_count": self.db.query(Question)
                .filter_by(record_id=exam.id)
                .count(),
                "status": exam.status.value,
                "exam_date": exam.exam_date.isoformat() if exam.exam_date else None,
            }
            for exam in rows
        ]
        return {"total": total, "page": page, "page_size": page_size, "items": items}
