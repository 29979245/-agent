"""练习 API L2 集成测试（task 5.1-5.4）。"""
from app.core.security import create_token
from app.db.models import (
    Account,
    AccountRole,
    Class,
    ExamRecord,
    Grade,
    ReviewTask,
    School,
    Student,
    StudentAnswer,
    Teacher,
)
from tests.integration.conftest import _account, _headers, _practice_exam, _student

Q1 = {"content": "Q1", "answer": "A", "kp": "氧化还原反应", "difficulty": "easy"}
Q2 = {"content": "Q2", "answer": "B", "kp": "化学平衡", "difficulty": "easy"}


def test_tasks_own_and_counts(exercise_client):
    client, db = exercise_client
    stu = _student(db)
    acc = _account(db, stu)
    _practice_exam(db, stu, questions=[Q1, Q2], extra_stats={"difficulty": "easy"})
    resp = client.get(f"/api/practice/student/{stu.id}/tasks", headers=_headers(acc))
    assert resp.status_code == 200
    body = resp.json()
    assert body["pending_count"] == 1 and body["completed_count"] == 0
    task = body["tasks"][0]
    assert task["practice_id"] and task["title"] == "自适应练习"
    assert task["question_count"] == 2
    assert task["status"] == "pending"
    assert set(task["knowledge_points"]) == {"氧化还原反应", "化学平衡"}
    assert task["difficulty"] == "easy"


def test_tasks_student_cross_403(exercise_client):
    client, db = exercise_client
    stu_a = _student(db, name="A")
    acc_a = _account(db, stu_a)
    stu_b = _student(db, name="B")
    _practice_exam(db, stu_b, questions=[Q1])
    resp = client.get(f"/api/practice/student/{stu_b.id}/tasks", headers=_headers(acc_a))
    assert resp.status_code == 403


def test_submit_grades_and_writes_answered_at(exercise_client):
    client, db = exercise_client
    stu = _student(db)
    acc = _account(db, stu)
    exam = _practice_exam(db, stu, questions=[Q1, Q2])
    resp = client.post(
        "/api/practice/submit",
        json={"practice_id": exam.id, "answers": [
            {"question_id": exam.questions[0].id, "selected_option": "A"},
            {"question_id": exam.questions[1].id, "selected_option": "A"},
        ]},
        headers=_headers(acc),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["score"] == 1 and body["total"] == 2
    assert body["accuracy"] == 0.5
    assert [r["is_correct"] for r in body["results"]] == [True, False]
    assert body["results"][1]["correct_answer"] == "B"
    answers = db.query(StudentAnswer).filter(StudentAnswer.exam_id == exam.id).all()
    assert len(answers) == 2
    assert all(a.answered_at is not None for a in answers)


def test_submit_other_exam_403_no_write(exercise_client):
    client, db = exercise_client
    stu_a = _student(db, name="A")
    acc_a = _account(db, stu_a)
    stu_b = _student(db, name="B")
    exam_b = _practice_exam(db, stu_b, questions=[Q1])
    resp = client.post(
        "/api/practice/submit",
        json={"practice_id": exam_b.id, "answers": [
            {"question_id": exam_b.questions[0].id, "selected_option": "A"},
        ]},
        headers=_headers(acc_a),
    )
    assert resp.status_code == 403
    assert db.query(StudentAnswer).count() == 0  # 不落库


def test_submit_missing_exam_404(exercise_client):
    client, db = exercise_client
    stu = _student(db)
    acc = _account(db, stu)
    resp = client.post(
        "/api/practice/submit",
        json={"practice_id": 99999, "answers": [{"question_id": 1, "selected_option": "A"}]},
        headers=_headers(acc),
    )
    assert resp.status_code == 404
    assert db.query(StudentAnswer).count() == 0


def test_effect_two_records_improvement(exercise_client):
    client, db = exercise_client
    stu = _student(db)
    acc = _account(db, stu)
    exam1 = _practice_exam(db, stu, questions=[Q1])
    exam2 = _practice_exam(db, stu, questions=[Q1, Q2])
    # exam2 最先：正确 1/2 → 0.5；exam1 正确 1/1 → 1.0
    from app.db.models import StudentAnswer
    q1 = exam2.questions[0].id
    q2 = exam2.questions[1].id
    for qid, correct in ((q1, True), (q2, False)):
        db.add(StudentAnswer(student_id=stu.id, question_id=qid, exam_id=exam2.id,
                             answer_text="A", is_correct=correct))
    q1a = exam1.questions[0].id
    db.add(StudentAnswer(student_id=stu.id, question_id=q1a, exam_id=exam1.id,
                         answer_text="A", is_correct=True))
    db.commit()
    resp = client.get(f"/api/practice/effect/{stu.id}", headers=_headers(acc))
    assert resp.status_code == 200
    body = resp.json()
    assert body["student_id"] == stu.id and body["student_name"] == "张三"
    imp = body["improvement"]
    assert imp["previous"]["accuracy"] == 1.0  # exam1 早
    assert imp["current"]["accuracy"] == 0.5   # exam2 晚
    assert imp["improvement"] == -0.5


def test_effect_less_than_two_empty(exercise_client):
    client, db = exercise_client
    stu = _student(db)
    acc = _account(db, stu)
    _practice_exam(db, stu, questions=[Q1])  # 仅一份记录
    resp = client.get(f"/api/practice/effect/{stu.id}", headers=_headers(acc))
    assert resp.status_code == 200
    assert resp.json()["improvement"] is None  # 不足两次不除零


def test_submit_side_effect_creates_review_task(exercise_client):
    """提交错题 → 后台副作用创建 ReviewTask（创建即到期）。"""
    client, db = exercise_client
    stu = _student(db)
    acc = _account(db, stu)
    exam = _practice_exam(db, stu, questions=[Q1])
    resp = client.post(
        "/api/practice/submit",
        json={"practice_id": exam.id, "answers": [
            {"question_id": exam.questions[0].id, "selected_option": "B"},
        ]},
        headers=_headers(acc),
    )
    assert resp.status_code == 200
    task = db.query(ReviewTask).filter_by(student_id=stu.id,
                                          question_id=exam.questions[0].id).first()
    assert task is not None
    assert task.status.value == "pending"
    assert task.next_review_at is not None  # 创建即到期


def test_submit_side_effect_dedup(exercise_client):
    """同一错题多次提交 → 仅一个 ReviewTask。"""
    client, db = exercise_client
    stu = _student(db)
    acc = _account(db, stu)
    exam = _practice_exam(db, stu, questions=[Q1])
    payload = {"practice_id": exam.id, "answers": [
        {"question_id": exam.questions[0].id, "selected_option": "B"},
    ]}
    assert client.post("/api/practice/submit", json=payload, headers=_headers(acc)).status_code == 200
    assert client.post("/api/practice/submit", json=payload, headers=_headers(acc)).status_code == 200
    assert db.query(ReviewTask).filter_by(student_id=stu.id).count() == 1


def test_diagnosis_fallback_not_blocking(exercise_client):
    """提交响应先行返回；后台诊断落规则单路或 error 标志。"""
    client, db = exercise_client
    stu = _student(db)
    acc = _account(db, stu)
    exam = _practice_exam(db, stu, questions=[Q1])
    resp = client.post(
        "/api/practice/submit",
        json={"practice_id": exam.id, "answers": [
            {"question_id": exam.questions[0].id, "selected_option": "B"},
        ]},
        headers=_headers(acc),
    )
    assert resp.status_code == 200  # 响应不因诊断阻塞
    ans = db.query(StudentAnswer).filter_by(exam_id=exam.id).first()
    assert ans.diagnosis_source in ("rule", "error")  # LLM 不可用 → 规则降级
    assert ans.diagnosis_version == "1.0"


def test_submit_no_token_401(exercise_client):
    client, db = exercise_client
    resp = client.post("/api/practice/submit", json={"practice_id": 1, "answers": []})
    assert resp.status_code == 401


# ---------------- 自适应练习确认落库（doc 28 §六 / design D2） ----------------

def _class_with_students(db, names):
    """建学校/年级/班级 + 学生 + 教师，返回 (cls, students, teacher_acc)。"""
    school = School(name="S")
    db.add(school)
    db.flush()
    grade = Grade(school_id=school.id, name="高一", academic_year="2026")
    db.add(grade)
    db.flush()
    cls = Class(grade_id=grade.id, name="1班")
    db.add(cls)
    db.flush()
    students = []
    for i, n in enumerate(names, 1):
        s = Student(class_id=cls.id, name=n, barrier_profile={"concept": 1.0})
        db.add(s)
        db.flush()
        students.append(s)
    t = Teacher(school_id=school.id, name="王老师", phone="13800000001")
    db.add(t)
    db.flush()
    acc = Account(username="t1", password_hash="x", role=AccountRole.teacher,
                  role_id=t.id)
    db.add(acc)
    db.flush()
    return cls, students, acc


def _teacher_headers(account):
    token = create_token(user_id=account.id, role="teacher", school_id=1)
    return {"Authorization": f"Bearer {token}"}


def _with_bank(tmp_path, content):
    """注入临时真题库并返回恢复器（防污染全局 bank）。"""
    from app.services.question import historical as h
    old = h._global_bank
    p = tmp_path / "全国卷" / "2024"
    p.mkdir(parents=True, exist_ok=True)
    (p / "真题.json").write_text(content, encoding="utf-8")
    h.reload_bank(tmp_path)
    return lambda: setattr(h, "_global_bank", old)


def test_adaptive_confirm_teacher_200_persists(exercise_client, tmp_path):
    """教师确认预览 → 逐生落库 ExamRecord + 复制题目（D2）。"""
    client, db = exercise_client
    restore = _with_bank(tmp_path, (
        '{"questions": [{"id": "q1", "content": "真题A", "answer": "B", '
        '"knowledge_points": ["氧化还原反应"], "difficulty": "easy", '
        '"options": ["A", "B", "C", "D"]}]}'
    ))
    try:
        cls, students, acc = _class_with_students(db, ["学生甲", "学生乙"])
        ref = "全国卷/2024/真题#q1"
        resp = client.post(
            "/api/practice/adaptive/confirm",
            json={"class_id": cls.id, "items": [
                {"student_id": students[0].id, "question_refs": [ref]},
                {"student_id": students[1].id, "question_refs": [ref]},
            ]},
            headers=_teacher_headers(acc),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["results"]) == 2
        assert {r["student_id"] for r in body["results"]} == {s.id for s in students}
        assert all(r["practice_id"] and r["question_count"] == 1 for r in body["results"])
        records = db.query(ExamRecord).filter(ExamRecord.student_id.isnot(None)).all()
        assert len(records) == 2
        assert all(len(e.questions) == 1 for e in records)
        assert all(e.questions[0].source.value == "practice" for e in records)
    finally:
        restore()


def test_adaptive_confirm_student_403(exercise_client):
    """学生持 practice/create 但非教师 → 403，不落库（D2 显式门控）。"""
    client, db = exercise_client
    stu = _student(db)
    acc = _account(db, stu)
    resp = client.post(
        "/api/practice/adaptive/confirm",
        json={"class_id": 1, "items": [
            {"student_id": stu.id, "question_refs": ["x"]},
        ]},
        headers=_headers(acc),
    )
    assert resp.status_code == 403
    assert db.query(ExamRecord).filter(ExamRecord.student_id.isnot(None)).count() == 0


def test_adaptive_confirm_invalid_ref_400_no_write(exercise_client, tmp_path):
    """非法 ref → 400，整批拒绝不落库。"""
    client, db = exercise_client
    restore = _with_bank(tmp_path, "")  # 空库 → ref 不可解析
    try:
        cls, students, acc = _class_with_students(db, ["学生甲"])
        resp = client.post(
            "/api/practice/adaptive/confirm",
            json={"class_id": cls.id, "items": [
                {"student_id": students[0].id, "question_refs": ["全国卷/2024/真题#nope"]},
            ]},
            headers=_teacher_headers(acc),
        )
        assert resp.status_code == 400
        assert resp.json()["error_code"] == "ADAPTIVE_CONFIRM_INVALID"
        assert db.query(ExamRecord).filter(ExamRecord.student_id.isnot(None)).count() == 0
    finally:
        restore()


def test_adaptive_confirm_empty_refs_400_no_write(exercise_client, tmp_path):
    """空 question_refs（preview 缺题短fall）→ 400 ADAPTIVE_CONFIRM_INVALID，整批不落库。

    修复 code-review 指出的 422 偏差：空 refs 应走 400/409 家族而非 pydantic 校验错误。
    """
    client, db = exercise_client
    restore = _with_bank(tmp_path, "")
    try:
        cls, students, acc = _class_with_students(db, ["学生甲"])
        resp = client.post(
            "/api/practice/adaptive/confirm",
            json={"class_id": cls.id, "items": [
                {"student_id": students[0].id, "question_refs": []},
            ]},
            headers=_teacher_headers(acc),
        )
        assert resp.status_code == 400
        assert resp.json()["error_code"] == "ADAPTIVE_CONFIRM_INVALID"
        assert db.query(ExamRecord).filter(ExamRecord.student_id.isnot(None)).count() == 0
    finally:
        restore()
