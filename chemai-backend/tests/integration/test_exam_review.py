"""教师端主观题人工复核 API 集成测试（teacher-grading，方案 B）。

- GET /api/exam/{id}/reviews：待复核清单（LLM 判不出的主观题作答），教师+，学校隔离
- POST /api/exam/{id}/answers/{aid}/review：逐题改判，落 is_correct/备注，清 review_needed
- 守卫：student 403 / 跨校 teacher 403 / 改判他考试作答 404
"""
import datetime

from app.core.security import create_token
from app.db.models import (
    Account,
    Class,
    ExamRecord,
    Grade,
    Question,
    School,
    Student,
    StudentAnswer,
    Teacher,
)
from app.db.models.enums import (
    AccountRole,
    AuditStatus,
    Difficulty,
    ExamStatus,
    ExamType,
    QuestionSource,
)
from tests.integration.conftest import exercise_client  # noqa: F401


def _school(db, name="A校"):
    s = School(name=name, current_semester="2026-1")
    db.add(s)
    db.flush()
    return s


def _grade(db, school, name="高一"):
    g = Grade(school_id=school.id, name=name, academic_year="2026")
    db.add(g)
    db.flush()
    return g


def _class(db, grade, name="1班"):
    c = Class(grade_id=grade.id, name=name)
    db.add(c)
    db.flush()
    return c


def _student(db, cls, name="张三"):
    s = Student(class_id=cls.id, name=name, barrier_profile={})
    db.add(s)
    db.flush()
    return s


def _teacher(db, school, name="王老师"):
    t = Teacher(school_id=school.id, name=name, phone=f"13{name.encode().hex()[:8]}")
    db.add(t)
    db.flush()
    acc = Account(username=f"t{name}", password_hash="x", role=AccountRole.teacher,
                  role_id=t.id)
    db.add(acc)
    db.flush()
    return t, acc


def _auth(account, role="teacher"):
    token = create_token(user_id=account.id, role=role, school_id=1)
    return {"Authorization": f"Bearer {token}"}


def _exam(db, cls, name="月考", status=ExamStatus.published):
    e = ExamRecord(class_id=cls.id, student_id=None, name=name, exam_type=ExamType.exam,
                   status=status, exam_date=datetime.date.today(),
                   question_stats={"published": status == ExamStatus.published})
    db.add(e)
    db.flush()
    return e


def _subjective_question(db, exam):
    q = Question(content="推断题：写出水的电解方程式", options=[], answer="2H2O = 2H2↑ + O2↑",
                 analysis="", knowledge_points="电解",
                 difficulty=Difficulty.medium, source=QuestionSource.manual,
                 audit_status=AuditStatus.passed, audit_report={}, record_id=exam.id)
    db.add(q)
    db.flush()
    return q


def _setup(db):
    """学校 A：班级 + 学生 + 同校教师 + 含主观题的发布考试。"""
    school = _school(db)
    grade = _grade(db, school)
    cls = _class(db, grade)
    stu = _student(db, cls)
    teacher, tacc = _teacher(db, school)
    stu_acc = Account(username=f"s{stu.id}", password_hash="x", role=AccountRole.student,
                      role_id=stu.id)
    db.add(stu_acc)
    db.flush()
    exam = _exam(db, cls)
    q = _subjective_question(db, exam)
    db.commit()
    return {"cls": cls, "stu": stu, "stu_acc": stu_acc, "teacher": teacher,
            "tacc": tacc, "exam": exam, "q": q}


def _submit_review_needed(client, db, ctx, is_correct=False, reason="LLM 不确定"):
    async def _fake_subjective(question, student_answer, standard_answer, client=None):
        return {"is_correct": is_correct, "review_needed": True, "reason": reason}

    import app.api.v1.exam as exam_module
    import pytest

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(exam_module, "grade_subjective", _fake_subjective)
    try:
        resp = client.post(
            f"/api/exam/{ctx['exam'].id}/submit",
            json={"answers": [{"question_id": ctx["q"].id, "selected_option": "2H2O=2H2+O2"}]},
            headers=_auth(ctx["stu_acc"], role="student"),
        )
    finally:
        monkeypatch.undo()
    assert resp.status_code == 200
    db.refresh(ctx["exam"])
    return resp.json()


# ---------------- 清单 + 改判 ----------------

def test_review_queue_and_correct(exercise_client):
    client, db = exercise_client
    ctx = _setup(db)
    body = _submit_review_needed(client, db, ctx)

    # 提交响应携带 review_needed（学生端"需人工复核"标签）
    assert body["results"][0]["review_needed"] is True

    # 教师拉待复核清单
    resp = client.get(f"/api/exam/{ctx['exam'].id}/reviews", headers=_auth(ctx["tacc"]))
    assert resp.status_code == 200
    d = resp.json()
    assert d["pending"] == 1 and len(d["items"]) == 1
    item = d["items"][0]
    assert item["student_name"] == "张三"
    assert "电解" in item["question_content"]
    assert item["standard_answer"] == "2H2O = 2H2↑ + O2↑"
    assert item["student_answer"] == "2H2O=2H2+O2"
    assert item["review_reason"] == "LLM 不确定"
    assert item["is_correct"] is False
    assert item["answer_id"] == db.query(StudentAnswer).filter_by(
        exam_id=ctx["exam"].id).first().id

    # 教师判对 + 备注
    resp = client.post(
        f"/api/exam/{ctx['exam'].id}/answers/{item['answer_id']}/review",
        json={"is_correct": True, "comment": "配平正确，箭头等价"},
        headers=_auth(ctx["tacc"]),
    )
    assert resp.status_code == 200
    assert resp.json()["is_correct"] is True
    assert resp.json()["review_needed"] is False

    # 队列已清空；DB 落库复核结果
    d = client.get(f"/api/exam/{ctx['exam'].id}/reviews", headers=_auth(ctx["tacc"])).json()
    assert d["pending"] == 0 and d["items"] == []
    ans = db.query(StudentAnswer).filter_by(exam_id=ctx["exam"].id).first()
    assert ans.is_correct is True
    assert ans.review_comment == "配平正确，箭头等价"
    assert ans.review_needed is False
    assert ans.reviewed_at is not None

    # 教师开始阅卷（in_progress→grading）→ 完成统计：wrong_counts 不含改判为对的题
    assert client.post(
        f"/api/exam/{ctx['exam'].id}/start-grading", headers=_auth(ctx["tacc"])
    ).status_code == 200
    resp = client.post(f"/api/exam/{ctx['exam'].id}/finalize", headers=_auth(ctx["tacc"]))
    assert resp.status_code == 200
    assert ctx["q"].id not in resp.json()["stats"]["wrong_counts"]


def test_review_queue_empty(exercise_client):
    client, db = exercise_client
    ctx = _setup(db)
    # 未提交 → 无作答 → 空队列
    d = client.get(f"/api/exam/{ctx['exam'].id}/reviews", headers=_auth(ctx["tacc"])).json()
    assert d["pending"] == 0 and d["items"] == []


# ---------------- 守卫 ----------------

def test_reviews_student_forbidden(exercise_client):
    client, db = exercise_client
    ctx = _setup(db)
    resp = client.get(f"/api/exam/{ctx['exam'].id}/reviews", headers=_auth(ctx["stu_acc"], role="student"))
    assert resp.status_code == 403


def test_reviews_cross_school_forbidden(exercise_client):
    client, db = exercise_client
    ctx = _setup(db)
    # B 校教师看 A 校考试复核清单 → 403
    b_school = _school(db, "B校")
    b_teacher, b_tacc = _teacher(db, b_school, "李老师")
    db.commit()
    resp = client.get(f"/api/exam/{ctx['exam'].id}/reviews", headers=_auth(b_tacc))
    assert resp.status_code == 403


def test_review_wrong_exam_404(exercise_client):
    client, db = exercise_client
    ctx = _setup(db)
    _submit_review_needed(client, db, ctx)
    # 另一场考试（同校）：用 B 考试的 URL 改判 A 考试的作答 → 404
    other = _exam(db, ctx["cls"], name="另一场")
    q2 = _subjective_question(db, other)
    db.commit()
    aid = db.query(StudentAnswer).filter_by(exam_id=ctx["exam"].id).first().id
    resp = client.post(
        f"/api/exam/{other.id}/answers/{aid}/review",
        json={"is_correct": True},
        headers=_auth(ctx["tacc"]),
    )
    assert resp.status_code == 404
