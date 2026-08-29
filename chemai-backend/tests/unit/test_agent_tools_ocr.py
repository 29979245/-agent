"""OCR 批改组 3 工具单测（tasks 2.3）：进度聚合 / 批量批改不落库 / 保存落库+诊断 / 角色与审批门控。"""
import asyncio
from datetime import date

import pytest

from app.agents.tools import tools_ocr
from app.agents.tools.context import ToolContext
from app.agents.tools.registry import execute_tool
from app.core.exceptions import ForbiddenError
from app.db.models import (
    Class,
    Difficulty,
    ExamRecord,
    ExamStatus,
    ExamType,
    Grade,
    OCRTask,
    OCRTaskStatus,
    Question,
    School,
    Student,
    StudentAnswer,
    UploadSession,
    UploadSessionStatus,
)


def run(coro):
    return asyncio.run(coro)


# ---------------- 造数 ----------------

def _org(db):
    school = School(name="测试中学")
    db.add(school)
    db.flush()
    grade = Grade(school_id=school.id, name="高一", academic_year="2026")
    db.add(grade)
    db.flush()
    cls = Class(grade_id=grade.id, name="高一(1)班", head_teacher="张老师")
    db.add(cls)
    db.flush()
    return school, grade, cls


def _exam(db, cls, answers=("A", "B")):
    exam = ExamRecord(
        class_id=cls.id, name="期中", status=ExamStatus.published,
        exam_type=ExamType.exam, exam_date=date.today(),
    )
    db.add(exam)
    db.flush()
    for i, ans in enumerate(answers):
        db.add(
            Question(
                content=f"第{i + 1}题", answer=ans, analysis="", knowledge_points="",
                difficulty=Difficulty.medium, record_id=exam.id,
            )
        )
    db.flush()
    return exam


def _session(db, school):
    s = UploadSession(status=UploadSessionStatus.uploaded, school_id=school.id)
    db.add(s)
    db.flush()
    return s


def _task(db, session, result=None, status=OCRTaskStatus.done):
    t = OCRTask(session_id=session.id, file_path="card.png", status=status, result=result or {})
    db.add(t)
    db.flush()
    return t


def _done_result(student_no="2023001234", name="张三"):
    return {
        "student_no": student_no,
        "student_name": name,
        "answers": [{"question_no": 1, "answer": "A"}, {"question_no": 2, "answer": "B"}],
    }


def _ctx(db, school, role="teacher"):
    return ToolContext(db=db, user={"user_id": 1, "role": role, "school_id": school.id})


# ---------------- query_ocr_progress ----------------

def test_query_ocr_progress_aggregates(db_session):
    school, _, _ = _org(db_session)
    s = _session(db_session, school)
    _task(db_session, s, {"student_no": "1"})
    _task(db_session, s, {"student_no": "2"})
    _task(db_session, s, {"student_no": "3"}, status=OCRTaskStatus.failed)
    db_session.commit()

    out = tools_ocr.query_ocr_progress(_ctx(db_session, school), batch_id=s.id)
    assert out["batch_id"] == s.id
    assert out["status"] == "uploaded"
    assert out["total"] == 3
    assert out["done"] == 2
    assert out["failed"] == 1
    assert len(out["tasks"]) == 3
    assert {t["status"] for t in out["tasks"]} == {"done", "failed"}


def test_query_ocr_progress_cross_school_forbidden(db_session):
    school, _, _ = _org(db_session)
    other = School(name="另一所学校")
    db_session.add(other)
    s = _session(db_session, other)
    db_session.commit()

    # 本校教师访问他校批次 → get_session_or_404 403
    ctx = _ctx(db_session, school)
    with pytest.raises(ForbiddenError):
        tools_ocr.query_ocr_progress(ctx, batch_id=s.id)


def test_query_ocr_progress_batch_missing_404(db_session):
    school, _, _ = _org(db_session)
    out = tools_ocr.query_ocr_progress(_ctx(db_session, school), batch_id=99999)
    assert out["error"] == "NOT_FOUND"


# ---------------- grade_answer_sheets ----------------

def test_grade_answer_sheets_not_persist(db_session):
    school, _, cls = _org(db_session)
    exam = _exam(db_session, cls, ("A", "B"))
    s = _session(db_session, school)
    _task(db_session, s, _done_result())
    db_session.commit()

    out = tools_ocr.grade_answer_sheets(_ctx(db_session, school), batch_id=s.id, exam_id=exam.id)
    assert out["graded"] == 1
    assert out["source"] == "bank"
    # 只算不落库：不写 StudentAnswer
    assert db_session.query(StudentAnswer).count() == 0
    db_session.refresh(s)
    assert s.status == UploadSessionStatus.grading


def test_grade_answer_sheets_cross_school_exam_forbidden(db_session):
    school, _, cls = _org(db_session)
    other = School(name="B 校")
    db_session.add(other)
    db_session.flush()
    other_grade = Grade(school_id=other.id, name="高一", academic_year="2026")
    db_session.add(other_grade)
    db_session.flush()
    other_cls = Class(grade_id=other_grade.id, name="B班")
    db_session.add(other_cls)
    db_session.flush()
    exam_b = _exam(db_session, other_cls, ("A", "B"))  # 他校考试（不同学校、独立年级）
    s = _session(db_session, school)
    _task(db_session, s, _done_result())
    db_session.commit()

    ctx = _ctx(db_session, school)
    with pytest.raises(ForbiddenError):
        tools_ocr.grade_answer_sheets(ctx, batch_id=s.id, exam_id=exam_b.id)


# ---------------- save_grading_results ----------------

def test_save_grading_results_writes_and_triggers_diagnosis(db_session, monkeypatch):
    school, _, cls = _org(db_session)
    exam = _exam(db_session, cls, ("A", "B"))
    db_session.add(Student(class_id=cls.id, name="张三", student_no="2023001234", barrier_profile={}))
    s = _session(db_session, school)
    _task(db_session, s, _done_result())
    db_session.commit()

    # 先批改（写 task.result['grading']），再保存
    tools_ocr.grade_answer_sheets(_ctx(db_session, school), batch_id=s.id, exam_id=exam.id)

    calls = []
    monkeypatch.setattr(tools_ocr, "get_diagnosis_client", lambda: object())
    monkeypatch.setattr(
        tools_ocr, "run_llm_batch",
        lambda db, eid, client, user: calls.append((eid, user)) or {"diagnosed": 1},
    )

    out = tools_ocr.save_grading_results(_ctx(db_session, school), batch_id=s.id, exam_id=exam.id)
    assert out["saved"] == 1
    assert out["status"] == "done"
    assert out["diagnosis"]["status"] == "triggered"
    assert db_session.query(StudentAnswer).count() == 2
    assert calls == [(exam.id, {"user_id": 1, "role": "teacher", "school_id": school.id})]


def test_save_grading_results_no_diagnosis_when_no_saved(db_session, monkeypatch):
    """模式2/3（未注册学生/无考试）saved=0 → 不触发诊断。"""
    school, _, _ = _org(db_session)
    s = _session(db_session, school)
    _task(db_session, s, _done_result())  # 未注册学生
    db_session.commit()

    tools_ocr.grade_answer_sheets(_ctx(db_session, school), batch_id=s.id)
    calls = []
    monkeypatch.setattr(tools_ocr, "run_llm_batch",
                        lambda db, eid, client, user: calls.append(eid))
    out = tools_ocr.save_grading_results(_ctx(db_session, school), batch_id=s.id)
    assert out["saved"] == 0
    assert "diagnosis" not in out
    assert calls == []


# ---------------- 角色门控 ----------------

def test_non_teacher_forbidden(db_session):
    school, _, _ = _org(db_session)
    s = _session(db_session, school)
    db_session.commit()

    ctx = _ctx(db_session, school, role="student")
    with pytest.raises(ForbiddenError):
        tools_ocr.query_ocr_progress(ctx, batch_id=s.id)
    with pytest.raises(ForbiddenError):
        tools_ocr.grade_answer_sheets(ctx, batch_id=s.id)
    with pytest.raises(ForbiddenError):
        tools_ocr.save_grading_results(ctx, batch_id=s.id)


# ---------------- 审批门控（registry 集成） ----------------

def test_grade_save_approval_blocked(db_session):
    school, _, _ = _org(db_session)
    s = _session(db_session, school)
    db_session.commit()

    ctx = _ctx(db_session, school)
    for name in ("grade_answer_sheets", "save_grading_results"):
        out = run(execute_tool(ctx, name, {"batch_id": s.id}))
        assert out["error"] == "requires_approval_blocked"
        assert out["requires_approval"] is True
        assert out["tool"] == name
        # 审批后执行键通过（越审批层，进入工具执行）——空批次下可能返回成功 dict 或其他业务错误
        ctx.safe_guard.mark_approved(name, {"batch_id": s.id})
        out2 = run(execute_tool(ctx, name, {"batch_id": s.id}))
        assert out2.get("error") != "requires_approval_blocked"


def test_ocr_batch_prerequisite_via_registry(db_session):
    school, _, _ = _org(db_session)
    ctx = _ctx(db_session, school)
    for name in ("query_ocr_progress", "grade_answer_sheets", "save_grading_results"):
        out = run(execute_tool(ctx, name, {}))
        assert out["error"] == "missing_prerequisites"
