"""复习 API L2 集成测试（task 6.1-6.2）。"""
import datetime

from app.db.models import Question, ReviewHistory, ReviewTask, StudentAnswer
from app.db.models.enums import Difficulty, ReviewLevel, ReviewTaskStatus, QuestionSource, AuditStatus
from tests.integration.conftest import _account, _headers, _student


def _question(db, content="Q", kp="氧化还原反应"):
    q = Question(content=content, options=["A", "B"], answer="A", analysis="解析",
                 knowledge_points=kp, difficulty=Difficulty.easy,
                 source=QuestionSource.practice, audit_status=AuditStatus.passed,
                 audit_report={}, record_id=None)
    db.add(q)
    db.flush()
    db.commit()
    return q


def _task(db, student, question, level=ReviewLevel.level1, status=ReviewTaskStatus.pending):
    t = ReviewTask(student_id=student.id, question_id=question.id, review_level=level,
                   status=status, next_review_at=datetime.datetime.utcnow())
    db.add(t)
    db.flush()
    db.commit()
    return t


def test_tasks_lists_due_excludes_done(exercise_client):
    client, db = exercise_client
    stu = _student(db)
    acc = _account(db, stu)
    q1 = _question(db, content="Q1")
    q2 = _question(db, content="Q2")
    q3 = _question(db, content="Q3")
    _task(db, stu, q1, status=ReviewTaskStatus.pending)
    _task(db, stu, q2, status=ReviewTaskStatus.overdue)
    _task(db, stu, q3, status=ReviewTaskStatus.done)
    resp = client.get(f"/api/review/tasks/{stu.id}", headers=_headers(acc))
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 2  # done 排除
    assert {t["question_id"] for t in body["tasks"]} == {q1.id, q2.id}
    assert body["tasks"][0]["content"]  # 含题目内容
    assert all(t["status"] in ("pending", "overdue") for t in body["tasks"])
    assert body["stats"] == {"due": 2, "done_today": 0, "mastered": 1}


def test_tasks_student_cross_403(exercise_client):
    client, db = exercise_client
    stu_a = _student(db, name="A")
    acc_a = _account(db, stu_a)
    stu_b = _student(db, name="B")
    _task(db, stu_b, _question(db, content="QB"))
    resp = client.get(f"/api/review/tasks/{stu_b.id}", headers=_headers(acc_a))
    assert resp.status_code == 403


def test_submit_upgrade_and_writes_only_history(exercise_client):
    client, db = exercise_client
    stu = _student(db)
    acc = _account(db, stu)
    q = _question(db)
    task = _task(db, stu, q, level=ReviewLevel.level1)
    for _ in range(2):
        resp = client.post("/api/review/submit", json={"review_task_id": task.id, "passed": True},
                           headers=_headers(acc))
        assert resp.status_code == 200
    body = resp.json()
    assert body["review_level"] == "level2"  # 连续答对 2 次升级
    db.refresh(task)
    assert task.review_level == ReviewLevel.level2
    assert db.query(ReviewHistory).filter_by(review_task_id=task.id).count() == 2
    assert db.query(StudentAnswer).count() == 0  # 只写 ReviewHistory


def test_submit_not_owned_403(exercise_client):
    client, db = exercise_client
    stu_a = _student(db, name="A")
    acc_a = _account(db, stu_a)
    stu_b = _student(db, name="B")
    task = _task(db, stu_b, _question(db, content="QB"))
    resp = client.post("/api/review/submit", json={"review_task_id": task.id, "passed": True},
                       headers=_headers(acc_a))
    assert resp.status_code == 403
    assert db.query(ReviewHistory).count() == 0
