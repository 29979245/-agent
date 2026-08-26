"""练习 API L2 集成测试（task 5.1-5.4）。"""
from app.db.models import ExamRecord, ReviewTask, StudentAnswer
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
