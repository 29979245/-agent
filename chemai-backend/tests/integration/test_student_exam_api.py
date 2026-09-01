"""学生在线作答班级考试 API 集成测试（student-exam-online，doc 22§2.3 / 25§6.3）。

- GET /api/exam/mine：本班 published/in_progress 待作答 + 该生已提交（含 completed）；
  他班/草稿/已完成未作答不出现，submitted 标记
- POST /api/exam/{id}/submit：归属/状态/重复校验；客观题确定性比对 + 主观题 LLM 语义批改；
  首生作答 published→in_progress；BackgroundTasks 副作用（复习+诊断+画像聚合）
- GET /api/exam/{id}/questions 学生跨班读题 403（加固回归）
- finalize 完成统计后兜底聚合该考试学生画像
"""
import datetime

from app.core.security import create_token
from app.db.models import (
    Account,
    AccountRole,
    ExamRecord,
    Question,
    School,
    StudentAnswer,
    Teacher,
)
from app.db.models.enums import (
    AuditStatus,
    Difficulty,
    ExamStatus,
    ExamType,
    QuestionSource,
)
from tests.integration.conftest import _account, _headers, _student

Q1 = {"content": "客观题1：催化剂的作用", "options": ["A", "B", "C", "D"], "answer": "B",
      "kp": "催化作用", "analysis": "催化剂改变化学反应速率"}
Q2 = {"content": "主观题：简述质量守恒定律", "options": [], "answer": "反应前后原子种类数目不变",
      "kp": "质量守恒", "analysis": ""}
Q3 = {"content": "客观题2：化学平衡", "options": ["A", "B", "C", "D"], "answer": "C",
      "kp": "化学平衡", "analysis": ""}


def _class_exam(db, class_id, questions=None, name="第一次月考",
                status=ExamStatus.published):
    """班级考试（class_id 非空、student_id 空），直接造到目标状态。"""
    exam = ExamRecord(
        class_id=class_id, student_id=None, name=name,
        exam_type=ExamType.exam, status=status,
        exam_date=datetime.date.today(),
        question_stats={"published": status == ExamStatus.published},
    )
    db.add(exam)
    db.flush()
    for q in questions or []:
        db.add(Question(
            content=q["content"], options=q.get("options", []), answer=q["answer"],
            analysis=q.get("analysis", ""), knowledge_points=q.get("kp", ""),
            difficulty=q.get("difficulty", Difficulty.medium),
            source=QuestionSource.manual, audit_status=AuditStatus.passed,
            audit_report={}, record_id=exam.id,
        ))
    db.flush()
    db.commit()
    return exam


def _teacher(db):
    school = School(name="TS")
    db.add(school)
    db.flush()
    t = Teacher(school_id=school.id, name="王老师",
                phone=f"138{len(db.query(Teacher).all()) + 1:08d}")
    db.add(t)
    db.flush()
    acc = Account(username=f"tea{t.id}", password_hash="x",
                  role=AccountRole.teacher, role_id=t.id)
    db.add(acc)
    db.flush()
    db.commit()
    return acc


def _teacher_headers(acc):
    token = create_token(user_id=acc.id, role="teacher", school_id=1)
    return {"Authorization": f"Bearer {token}"}


# ---------------- GET /api/exam/mine ----------------

def test_mine_lists_own_class_exams_and_excludes_others(exercise_client):
    client, db = exercise_client
    stu = _student(db)
    acc = _account(db, stu)
    exam = _class_exam(db, stu.class_id, questions=[Q1, Q2])
    other = _student(db, name="他班")
    other_exam = _class_exam(db, other.class_id, questions=[Q1])
    _class_exam(db, stu.class_id, questions=[Q1], status=ExamStatus.draft)
    _class_exam(db, stu.class_id, questions=[Q1], status=ExamStatus.completed)

    resp = client.get("/api/exam/mine", headers=_headers(acc))
    assert resp.status_code == 200
    items = resp.json()["items"]
    ids = [i["exam_id"] for i in items]
    assert exam.id in ids                      # 本班已发布
    assert other_exam.id not in ids            # 他班不出现
    assert ExamStatus.draft not in ids         # 草稿不出现
    mine = next(i for i in items if i["exam_id"] == exam.id)
    assert mine["name"] == "第一次月考"
    assert mine["question_count"] == 2
    assert mine["submitted"] is False
    assert mine["status"] == "published"


def test_mine_marks_submitted_after_answer(exercise_client):
    client, db = exercise_client
    stu = _student(db)
    acc = _account(db, stu)
    exam = _class_exam(db, stu.class_id, questions=[Q1])
    q = exam.questions[0]
    db.add(StudentAnswer(student_id=stu.id, question_id=q.id, exam_id=exam.id,
                         answer_text="B", is_correct=True))
    db.commit()

    resp = client.get("/api/exam/mine", headers=_headers(acc))
    items = resp.json()["items"]
    assert len(items) == 1 and items[0]["exam_id"] == exam.id
    assert items[0]["submitted"] is True


# ---------------- POST /api/exam/{id}/submit ----------------

def test_submit_grades_objective_and_transitions(exercise_client):
    client, db = exercise_client
    stu = _student(db)
    acc = _account(db, stu)
    exam = _class_exam(db, stu.class_id, questions=[Q1, Q3])
    q1, q3 = exam.questions[0], exam.questions[1]

    resp = client.post(
        f"/api/exam/{exam.id}/submit",
        json={"answers": [
            {"question_id": q1.id, "selected_option": "B"},   # 正确
            {"question_id": q3.id, "selected_option": "A"},   # 错误
        ]},
        headers=_headers(acc),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["score"] == 1 and body["total"] == 2
    assert body["accuracy"] == 0.5
    assert [r["is_correct"] for r in body["results"]] == [True, False]
    assert body["results"][1]["correct_answer"] == "C"
    assert body["results"][1]["analysis"] == ""

    db.refresh(exam)
    assert exam.status == ExamStatus.in_progress  # 首生作答 → in_progress
    answers = db.query(StudentAnswer).filter(StudentAnswer.exam_id == exam.id).all()
    assert len(answers) == 2
    assert all(a.exam_id == exam.id and a.answered_at is not None for a in answers)


def test_submit_subjective_uses_llm_grade(exercise_client, monkeypatch):
    client, db = exercise_client
    stu = _student(db)
    acc = _account(db, stu)
    exam = _class_exam(db, stu.class_id, questions=[Q2])
    q = exam.questions[0]

    async def _fake_subjective(question, student_answer, standard_answer, client=None):
        return {"is_correct": True, "review_needed": True, "reason": "答案语义一致"}

    monkeypatch.setattr("app.api.v1.exam.grade_subjective", _fake_subjective)
    resp = client.post(
        f"/api/exam/{exam.id}/submit",
        json={"answers": [{"question_id": q.id, "selected_option": "原子种类不变"}]},
        headers=_headers(acc),
    )
    assert resp.status_code == 200
    r = resp.json()["results"][0]
    assert r["is_correct"] is True
    assert r["review_needed"] is True
    assert r["reason"] == "答案语义一致"
    assert resp.json()["score"] == 1
    # review_needed/reason 落库（教师端复核清单的数据源）
    persisted = db.query(StudentAnswer).filter(StudentAnswer.exam_id == exam.id).first()
    assert persisted.review_needed is True
    assert persisted.review_reason == "答案语义一致"


def test_submit_duplicate_409(exercise_client):
    client, db = exercise_client
    stu = _student(db)
    acc = _account(db, stu)
    exam = _class_exam(db, stu.class_id, questions=[Q1])
    q = exam.questions[0]
    payload = {"answers": [{"question_id": q.id, "selected_option": "B"}]}
    assert client.post(f"/api/exam/{exam.id}/submit", json=payload,
                       headers=_headers(acc)).status_code == 200
    resp = client.post(f"/api/exam/{exam.id}/submit", json=payload, headers=_headers(acc))
    assert resp.status_code == 409
    assert resp.json()["error_code"] == "EXAM_ALREADY_SUBMITTED"


def test_submit_cross_class_403(exercise_client):
    client, db = exercise_client
    stu_a = _student(db, name="A")
    acc_a = _account(db, stu_a)
    stu_b = _student(db, name="B")
    exam_b = _class_exam(db, stu_b.class_id, questions=[Q1])
    q = exam_b.questions[0]
    resp = client.post(
        f"/api/exam/{exam_b.id}/submit",
        json={"answers": [{"question_id": q.id, "selected_option": "B"}]},
        headers=_headers(acc_a),
    )
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "EXAM_NOT_IN_CLASS"
    assert db.query(StudentAnswer).filter_by(exam_id=exam_b.id).count() == 0


def test_submit_teacher_403(exercise_client):
    client, db = exercise_client
    stu = _student(db)
    exam = _class_exam(db, stu.class_id, questions=[Q1])
    q = exam.questions[0]
    tea = _teacher(db)
    resp = client.post(
        f"/api/exam/{exam.id}/submit",
        json={"answers": [{"question_id": q.id, "selected_option": "B"}]},
        headers=_teacher_headers(tea),
    )
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "STUDENT_ONLY"


def test_submit_closed_exam_409(exercise_client):
    client, db = exercise_client
    stu = _student(db)
    acc = _account(db, stu)
    exam = _class_exam(db, stu.class_id, questions=[Q1], status=ExamStatus.grading)
    q = exam.questions[0]
    resp = client.post(
        f"/api/exam/{exam.id}/submit",
        json={"answers": [{"question_id": q.id, "selected_option": "B"}]},
        headers=_headers(acc),
    )
    assert resp.status_code == 409
    assert resp.json()["error_code"] == "EXAM_NOT_OPEN"


def test_submit_no_token_401(exercise_client):
    client, db = exercise_client
    stu = _student(db)
    exam = _class_exam(db, stu.class_id, questions=[Q1])
    q = exam.questions[0]
    resp = client.post(
        f"/api/exam/{exam.id}/submit",
        json={"answers": [{"question_id": q.id, "selected_option": "B"}]},
    )
    assert resp.status_code == 401


# ---------------- 越权加固回归 ----------------

def test_list_questions_cross_class_403(exercise_client):
    client, db = exercise_client
    stu_a = _student(db, name="A")
    acc_a = _account(db, stu_a)
    stu_b = _student(db, name="B")
    exam_b = _class_exam(db, stu_b.class_id, questions=[Q1])
    resp = client.get(f"/api/exam/{exam_b.id}/questions", headers=_headers(acc_a))
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "EXAM_NOT_IN_CLASS"


# ---------------- 画像聚合 ----------------

def test_submit_side_effect_refreshes_profile(exercise_client, monkeypatch):
    client, db = exercise_client
    stu = _student(db)
    acc = _account(db, stu)
    exam = _class_exam(db, stu.class_id, questions=[Q1])
    q = exam.questions[0]
    calls = []

    def _spy_refresh(_db, student_ids):
        calls.append(list(student_ids))

    monkeypatch.setattr(
        "app.services.exercise.side_effects.refresh_profiles", _spy_refresh
    )
    resp = client.post(
        f"/api/exam/{exam.id}/submit",
        json={"answers": [{"question_id": q.id, "selected_option": "A"}]},  # 答错 → 走诊断
        headers=_headers(acc),
    )
    assert resp.status_code == 200
    assert calls == [[stu.id]]


def test_finalize_refreshes_attendee_profiles(exercise_client, monkeypatch):
    client, db = exercise_client
    stu = _student(db)
    acc = _account(db, stu)
    tea = _teacher(db)
    exam = _class_exam(db, stu.class_id, questions=[Q1])
    q = exam.questions[0]
    # 学生先作答（published→in_progress）→ 教师触发阅卷（→grading）→ 完成统计
    assert client.post(
        f"/api/exam/{exam.id}/submit",
        json={"answers": [{"question_id": q.id, "selected_option": "B"}]},
        headers=_headers(acc),
    ).status_code == 200
    assert client.post(
        f"/api/exam/{exam.id}/start-grading", headers=_teacher_headers(tea)
    ).status_code == 200

    calls = []

    def _spy_refresh(_db, student_ids):
        calls.append(list(student_ids))

    monkeypatch.setattr("app.api.v1.exam.refresh_profiles", _spy_refresh)
    resp = client.post(
        f"/api/exam/{exam.id}/finalize", headers=_teacher_headers(tea)
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "completed"
    assert resp.json()["stats"]["attended"] == 1
    assert calls == [[stu.id]]
