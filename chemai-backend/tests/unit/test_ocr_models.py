"""OCR 链模型测试：UploadSession/OCRTask/StudentSubmission（6.1-6.3）。"""
import datetime

import pytest
from sqlalchemy.exc import IntegrityError

from app.db.models import (
    Class,
    ExamRecord,
    Grade,
    OCRTask,
    School,
    StudentSubmission,
    UploadSession,
)
from app.db.models.enums import ExamType, OCRTaskStatus, UploadSessionStatus


def _session(db_session):
    s = UploadSession()
    db_session.add(s)
    db_session.flush()
    return s


def _exam(db_session):
    school = School(name="S")
    db_session.add(school)
    db_session.flush()
    grade = Grade(school_id=school.id, name="高一", academic_year="2026")
    db_session.add(grade)
    db_session.flush()
    cls = Class(grade_id=grade.id, name="1班")
    db_session.add(cls)
    db_session.flush()
    exam = ExamRecord(class_id=cls.id, exam_type=ExamType.exam, exam_date=datetime.date(2026, 8, 22))
    db_session.add(exam)
    db_session.flush()
    return exam


# ---- 6.1 UploadSession ----

def test_upload_session_defaults(db_session):
    s = _session(db_session)
    db_session.commit()
    got = db_session.get(UploadSession, s.id)
    assert got.status == UploadSessionStatus.uploaded
    assert got.degraded is False
    assert got.version == 0


def test_upload_session_state_transition(db_session):
    s = _session(db_session)
    s.status = UploadSessionStatus.grading
    db_session.commit()
    assert db_session.get(UploadSession, s.id).status == UploadSessionStatus.grading


def test_upload_session_invalid_status_rejected(db_session):
    s = UploadSession(status="not_a_state")
    db_session.add(s)
    with pytest.raises(IntegrityError):
        db_session.flush()


# ---- 6.2 OCRTask ----

def test_ocr_task_state_transition(db_session):
    s = _session(db_session)
    task = OCRTask(session_id=s.id, status=OCRTaskStatus.pending)
    db_session.add(task)
    db_session.flush()
    task.status = OCRTaskStatus.processing
    task.progress = 50
    db_session.commit()
    task.status = OCRTaskStatus.done
    task.progress = 100
    task.result = {"correct": 8, "wrong": 2}
    db_session.commit()
    got = db_session.get(OCRTask, task.id)
    assert got.status == OCRTaskStatus.done
    assert got.result == {"correct": 8, "wrong": 2}


def test_ocr_task_failed_path(db_session):
    s = _session(db_session)
    task = OCRTask(session_id=s.id)
    db_session.add(task)
    db_session.flush()
    task.status = OCRTaskStatus.failed
    task.error = "OCR engine timeout"
    db_session.commit()
    got = db_session.get(OCRTask, task.id)
    assert got.status == OCRTaskStatus.failed
    assert "timeout" in got.error


# ---- 6.3 StudentSubmission ----

def test_student_submission_crud(db_session):
    s = _session(db_session)
    exam = _exam(db_session)
    sub = StudentSubmission(exam_id=exam.id, session_id=s.id)
    sub.answer_list = [{"q": 1, "ans": "A", "correct": True}]
    sub.total_score = 85
    db_session.add(sub)
    db_session.commit()
    got = db_session.get(StudentSubmission, sub.id)
    assert got.total_score == 85
    assert got.answer_list[0]["ans"] == "A"


def test_delete_session_cascades_tasks_and_submissions(db_session):
    s = _session(db_session)
    exam = _exam(db_session)
    db_session.add(OCRTask(session_id=s.id))
    db_session.add(StudentSubmission(exam_id=exam.id, session_id=s.id))
    db_session.flush()
    db_session.delete(s)
    db_session.commit()
    assert db_session.query(OCRTask).count() == 0
    assert db_session.query(StudentSubmission).count() == 0


def test_submission_allows_no_exam(db_session):
    """模式2/3 无考试提交：exam_id 可空（ADR 0003 只落 StudentSubmission）。"""
    s = _session(db_session)
    db_session.add(StudentSubmission(session_id=s.id))
    db_session.commit()
    got = db_session.query(StudentSubmission).filter_by(session_id=s.id).one()
    assert got.exam_id is None
