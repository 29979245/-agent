"""记忆组工具单测（tasks 3.3）：学生读自身记忆 / 跨学生 403 / 教师读偏好 / 诊断写记忆后读回。"""
import asyncio
from datetime import date

import pytest

from app.agents.memory import DIAGNOSIS_HISTORY_MAX_ITEMS, LongTermStore, MemorySystem
from app.agents.tools import tools_diagnosis, tools_memory, tools_ocr
from app.agents.tools.context import ToolContext
from app.agents.tools.registry import execute_tool
from app.core.exceptions import ForbiddenError
from app.db.models import (
    Account,
    Class,
    Difficulty,
    ExamRecord,
    ExamStatus,
    ExamType,
    Grade,
    OCRTask,
    OCRTaskStatus,
    Parent,
    Question,
    School,
    Student,
    StudentAnswer,
    StudentParentBinding,
    UploadSession,
    UploadSessionStatus,
)
from app.db.models.enums import AccountRole


def run(coro):
    return asyncio.run(coro)


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


def _student(db, cls, name="张三", student_no="2023001234", profile=None):
    s = Student(
        class_id=cls.id, name=name, student_no=student_no,
        barrier_profile=profile or {"concept": 0.6, "reading": 0.3, "expression": 0.1},
        learning_plan={"title": "氧化还原专项计划", "steps": ["复习化合价", "练习配平"]},
    )
    db.add(s)
    db.flush()
    return s


def _account(db, role: AccountRole, role_id: int) -> Account:
    acc = Account(
        username=f"u_{role.value}_{role_id}", password_hash="x",
        role=role, role_id=role_id,
    )
    db.add(acc)
    db.flush()
    return acc


def _ctx(db, user, memory=None):
    return ToolContext(db=db, user=user, memory=memory)


def _mem(tmp_path) -> MemorySystem:
    return MemorySystem(long_term_store=LongTermStore(db_path=str(tmp_path / "mem.db")))


def _store(tmp_path) -> LongTermStore:
    return LongTermStore(db_path=str(tmp_path / "mem.db"))


# ---------------- 学生读自身记忆 ----------------

def test_memory_student_get_returns_last5_history_and_plan(db_session, tmp_path):
    school, _, cls = _org(db_session)
    stu = _student(db_session, cls)
    db_session.commit()

    store = _store(tmp_path)
    for i in range(7):  # 预置 7 条 → 只回最近 5 条
        store.push_student_diagnosis(stu.id, {"i": i, "source": "seed"})
    ctx = _ctx(db_session, {"user_id": 1, "role": "teacher", "school_id": school.id},
               memory=MemorySystem(long_term_store=store))

    out = tools_memory.memory_student_get(ctx, student_id=stu.id)
    assert out["student_id"] == stu.id
    assert out["name"] == "张三"
    assert [h["i"] for h in out["diagnosis_history"]] == [2, 3, 4, 5, 6]
    assert len(out["diagnosis_history"]) == DIAGNOSIS_HISTORY_MAX_ITEMS
    assert out["learning_plan"]["title"] == "氧化还原专项计划"


def test_memory_student_get_student_self_allowed(db_session, tmp_path):
    school, _, cls = _org(db_session)
    stu = _student(db_session, cls)
    acc = _account(db_session, AccountRole.student, stu.id)
    db_session.commit()

    ctx = _ctx(db_session, {"user_id": acc.id, "role": "student", "school_id": school.id},
               memory=_mem(tmp_path))
    out = tools_memory.memory_student_get(ctx, student_id=stu.id)
    assert out["student_id"] == stu.id
    assert out["diagnosis_history"] == []
    assert out["learning_plan"]["title"] == "氧化还原专项计划"


def test_memory_student_get_self_default(db_session, tmp_path):
    """学生缺省 student_id：自动解析本人（Account.role_id → Student.id），无需显式提供。"""
    school, _, cls = _org(db_session)
    stu = _student(db_session, cls)
    acc = _account(db_session, AccountRole.student, stu.id)
    db_session.commit()

    ctx = _ctx(db_session, {"user_id": acc.id, "role": "student", "school_id": school.id},
               memory=_mem(tmp_path))
    out = tools_memory.memory_student_get(ctx)  # 不传 student_id
    assert out["student_id"] == stu.id
    assert out["name"] == "张三"
    assert out["learning_plan"]["title"] == "氧化还原专项计划"


def test_memory_student_get_self_default_non_student_cannot_resolve(db_session, tmp_path):
    """非学生角色缺省 student_id：无法解析本人 → not_found（引导显式提供）。"""
    school, _, _ = _org(db_session)
    db_session.commit()
    ctx = _ctx(db_session, {"user_id": 1, "role": "teacher", "school_id": school.id}, memory=_mem(tmp_path))
    out = tools_memory.memory_student_get(ctx)
    assert out["error"] == "not_found"


def test_memory_student_get_cross_student_forbidden(db_session, tmp_path):
    school, _, cls = _org(db_session)
    stu_a = _student(db_session, cls, name="张三", student_no="2023001234")
    stu_b = _student(db_session, cls, name="李四", student_no="2023005678")
    acc = _account(db_session, AccountRole.student, stu_a.id)
    db_session.commit()

    ctx = _ctx(db_session, {"user_id": acc.id, "role": "student", "school_id": school.id},
               memory=_mem(tmp_path))
    # 学生仅可读自身（Account.role_id 指向 stu_a），读 stu_b → ForbiddenError
    with pytest.raises(ForbiddenError):
        tools_memory.memory_student_get(ctx, student_id=stu_b.id)


def test_memory_student_get_parent_bound_child_only(db_session, tmp_path):
    """家长仅可读已绑定子女（Spec 3.6 parent 门控）：绑定子女放行，未绑定 → ForbiddenError。"""
    school, _, cls = _org(db_session)
    bound = _student(db_session, cls, name="小明", student_no="2023001001")
    unbound = _student(db_session, cls, name="小红", student_no="2023001002")
    parent = Parent(name="明爸", phone="13800000001")
    db_session.add(parent)
    db_session.flush()
    db_session.add(StudentParentBinding(parent_id=parent.id, student_id=bound.id, bind_code="123456", status="active"))
    acc = _account(db_session, AccountRole.parent, parent.id)
    db_session.commit()

    ctx = _ctx(db_session, {"user_id": acc.id, "role": "parent", "school_id": school.id}, memory=_mem(tmp_path))
    out = tools_memory.memory_student_get(ctx, student_id=bound.id)
    assert out["student_id"] == bound.id
    with pytest.raises(ForbiddenError):
        tools_memory.memory_student_get(ctx, student_id=unbound.id)


def test_memory_student_get_missing_student(db_session, tmp_path):
    school, _, _ = _org(db_session)
    db_session.commit()
    ctx = _ctx(db_session, {"user_id": 1, "role": "teacher", "school_id": school.id},
               memory=_mem(tmp_path))
    out = tools_memory.memory_student_get(ctx, student_id=99999)
    assert out["error"] == "not_found"


# ---------------- 教师读偏好 ----------------

def test_memory_teacher_get_pref(db_session, tmp_path):
    school, _, _ = _org(db_session)
    db_session.commit()
    store = _store(tmp_path)
    store.set_teacher_pref(7, {"style": "启发式", "difficulty": "medium", "classes": ["高一(1)班"]})

    ctx = _ctx(db_session, {"user_id": 7, "role": "teacher", "school_id": school.id},
               memory=MemorySystem(long_term_store=store))
    out = tools_memory.memory_teacher_get(ctx)
    assert out["teacher_id"] == 7
    assert out["pref"]["style"] == "启发式"
    assert out["pref"]["difficulty"] == "medium"


def test_memory_teacher_get_non_teacher_forbidden(db_session, tmp_path):
    school, _, _ = _org(db_session)
    db_session.commit()
    ctx = _ctx(db_session, {"user_id": 7, "role": "student", "school_id": school.id},
               memory=_mem(tmp_path))
    with pytest.raises(ForbiddenError):
        tools_memory.memory_teacher_get(ctx)


# ---------------- 诊断写记忆后读回 ----------------

def test_diagnose_barrier_writes_memory_then_read_back(db_session, tmp_path):
    """诊断写记忆①：diagnose_barrier 个体诊断 → 画像快照写长期记忆 → memory_student_get 读回。"""
    school, _, cls = _org(db_session)
    stu = _student(db_session, cls)
    db_session.commit()

    ctx = _ctx(db_session, {"user_id": 1, "role": "teacher", "school_id": school.id},
               memory=_mem(tmp_path))
    out = run(tools_diagnosis.diagnose_barrier(ctx, student_id=stu.id))
    assert out["student_id"] == stu.id
    assert out["dominant_barrier"] == "concept"

    hist = tools_memory.memory_student_get(ctx, student_id=stu.id)["diagnosis_history"]
    assert len(hist) == 1
    assert hist[0]["source"] == "diagnose_barrier"
    assert hist[0]["dominant_barrier"] == "concept"
    assert hist[0]["barrier_profile"] == {"concept": 0.6, "reading": 0.3, "expression": 0.1}


def test_ocr_save_diagnosis_writes_memory_then_read_back(db_session, tmp_path, monkeypatch):
    """诊断写记忆②：OCR 保存触发诊断（含规则/LLM 降级路径）→ 已诊断学生画像写长期记忆 → 读回。"""
    school, _, cls = _org(db_session)
    exam = ExamRecord(
        class_id=cls.id, name="期中", status=ExamStatus.published,
        exam_type=ExamType.exam, exam_date=date.today(),
    )
    db_session.add(exam)
    db_session.flush()
    for i, ans in enumerate(("B", "B")):
        db_session.add(Question(
            content=f"第{i + 1}题", answer=ans, analysis="", knowledge_points="",
            difficulty=Difficulty.medium, record_id=exam.id,
        ))
    stu = _student(db_session, cls)
    db_session.flush()
    s = UploadSession(status=UploadSessionStatus.uploaded, school_id=school.id)
    db_session.add(s)
    db_session.flush()
    db_session.add(OCRTask(
        session_id=s.id, file_path="card.png", status=OCRTaskStatus.done,
        result={
            "student_no": stu.student_no,
            "student_name": stu.name,
            "answers": [{"question_no": 1, "answer": "A"}, {"question_no": 2, "answer": "B"}],
        },
    ))
    db_session.commit()

    tools_ocr.grade_answer_sheets(_ctx(db_session, {"user_id": 1, "role": "teacher", "school_id": school.id}),
                                  batch_id=s.id, exam_id=exam.id)

    # LLM 降级/规则诊断路径：mock run_llm_batch 直接对错误作答写 barrier_type（等价真实融合落库）
    def fake_run_llm_batch(db, exam_id, client, user):
        rows = db.query(StudentAnswer).filter(StudentAnswer.exam_id == exam_id).all()
        for ans in rows:
            if not ans.is_correct:
                ans.barrier_type = "concept"
        return {"diagnosed": 1, "failed": 0, "failures": [], "remaining": 0}

    monkeypatch.setattr(tools_ocr, "get_diagnosis_client", lambda: object())
    monkeypatch.setattr(tools_ocr, "run_llm_batch", fake_run_llm_batch)

    ctx = _ctx(db_session, {"user_id": 1, "role": "teacher", "school_id": school.id}, memory=_mem(tmp_path))
    out = tools_ocr.save_grading_results(ctx, batch_id=s.id, exam_id=exam.id)
    assert out["saved"] == 1
    assert out["diagnosis"]["status"] == "triggered"
    assert out["diagnosis"]["memory_written"] == 1

    hist = tools_memory.memory_student_get(ctx, student_id=stu.id)["diagnosis_history"]
    assert len(hist) == 1
    assert hist[0]["source"] == "ocr_save_diagnosis"
    assert hist[0]["dominant_barrier"] == "concept"
    assert {"ts", "source", "barrier_profile", "dominant_barrier", "dominant_label"} <= set(hist[0])


def test_memory_write_best_effort_no_crash(db_session):
    """写记忆 best-effort：memory 未注入 / 学生缺失 → 工具正常返回，不抛异常。"""
    school, _, cls = _org(db_session)
    stu = _student(db_session, cls)
    db_session.commit()

    ctx_no_mem = _ctx(db_session, {"user_id": 1, "role": "teacher", "school_id": school.id})
    out = run(tools_diagnosis.diagnose_barrier(ctx_no_mem, student_id=stu.id))
    assert out["student_id"] == stu.id
    assert tools_memory.memory_student_get(ctx_no_mem, student_id=stu.id)["diagnosis_history"] == []


# ---------------- Guard 集成（registry） ----------------

def test_memory_student_get_prerequisite_via_registry(db_session):
    school, _, _ = _org(db_session)
    ctx = _ctx(db_session, {"user_id": 1, "role": "teacher", "school_id": school.id})
    out = run(execute_tool(ctx, "memory_student_get", {}))
    assert out["error"] == "missing_prerequisites"


def test_memory_teacher_get_forbidden_via_registry(db_session):
    school, _, _ = _org(db_session)
    ctx = _ctx(db_session, {"user_id": 1, "role": "student", "school_id": school.id})
    out = run(execute_tool(ctx, "memory_teacher_get", {}))
    assert out["error"] == "forbidden"
