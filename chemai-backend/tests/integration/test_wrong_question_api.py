"""错题强化 API L2 集成测试（task 7.1-7.4）。"""
import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.models import (
    ExamRecord,
    ExamType,
    ExamStatus,
    Question,
    ReviewTask,
    StudentAnswer,
)
from app.db.models.enums import Difficulty, QuestionSource, AuditStatus, ReviewTaskStatus
from app.db.session import get_db
from app.main import app
from app.services.question.historical import reload_bank
from tests.integration.conftest import _account, _headers, _practice_exam, _student

Q1 = {"content": "真题A", "answer": "A", "kp": "氧化还原反应", "difficulty": "easy"}
Q2 = {"content": "真题B", "answer": "B", "kp": "氧化还原反应", "difficulty": "easy"}
Q3 = {"content": "真题C", "answer": "C", "kp": "化学平衡", "difficulty": "easy"}


def _seed_answers(db, stu, exam, wrong_qids, correct_qids=()):
    for qid in wrong_qids:
        db.add(StudentAnswer(student_id=stu.id, question_id=qid, exam_id=exam.id,
                             answer_text="x", is_correct=False, answered_at=datetime.datetime.utcnow()))
    for qid in correct_qids:
        db.add(StudentAnswer(student_id=stu.id, question_id=qid, exam_id=exam.id,
                             answer_text="A", is_correct=True, answered_at=datetime.datetime.utcnow()))
    db.commit()


def _load_bank(tmp_path):
    p = tmp_path / "全国卷" / "2024"
    p.mkdir(parents=True, exist_ok=True)
    (p / "真题.json").write_text('{"questions": ['
        '{"id": "q1", "content": "真题A", "answer": "B", "knowledge_points": ["氧化还原反应"], "difficulty": "easy"},'
        '{"id": "q2", "content": "真题B", "answer": "C", "knowledge_points": ["氧化还原反应"], "difficulty": "easy"},'
        '{"id": "q3", "content": "真题C", "answer": "D", "knowledge_points": ["化学平衡"], "difficulty": "easy"}'
        "]}", encoding="utf-8")
    return reload_bank(tmp_path)


def test_wrong_list_dedup_error_count(exercise_client):
    client, db = exercise_client
    stu = _student(db)
    acc = _account(db, stu)
    exam = _practice_exam(db, stu, questions=[Q1, Q2])
    q1, q2 = exam.questions[0].id, exam.questions[1].id
    _seed_answers(db, stu, exam, wrong_qids=[q1, q1, q2])
    resp = client.get(f"/api/wrong-questions/{stu.id}", headers=_headers(acc))
    assert resp.status_code == 200
    body = resp.json()
    by_q = {i["question_id"]: i for i in body["items"]}
    assert set(by_q) == {q1, q2}  # 去重
    assert by_q[q1]["error_count"] == 2
    assert by_q[q2]["error_count"] == 1
    assert body["count"] == 2
    assert body["stats"]["total"] == 2
    assert body["stats"]["week_new"] == 2
    assert body["stats"]["mastered"] == 0


def test_wrong_list_empty(exercise_client):
    client, db = exercise_client
    stu = _student(db)
    acc = _account(db, stu)
    resp = client.get(f"/api/wrong-questions/{stu.id}", headers=_headers(acc))
    assert resp.status_code == 200
    assert resp.json()["items"] == []


def test_wrong_list_student_cross_403(exercise_client):
    client, db = exercise_client
    stu_a = _student(db, name="A")
    acc_a = _account(db, stu_a)
    stu_b = _student(db, name="B")
    resp = client.get(f"/api/wrong-questions/{stu_b.id}", headers=_headers(acc_a))
    assert resp.status_code == 403


def test_variants_sampling_excludes_original(exercise_client, tmp_path):
    _load_bank(tmp_path)
    client, db = exercise_client
    stu = _student(db)
    acc = _account(db, stu)
    original = Question(content="真题A", options=["A", "B"], answer="A", knowledge_points="氧化还原反应",
                        difficulty=Difficulty.easy, source=QuestionSource.practice,
                        audit_status=AuditStatus.passed, audit_report={}, record_id=None)
    db.add(original)
    db.commit()
    resp = client.post("/api/wrong-questions/variants",
                       json={"question_id": original.id, "count": 2},
                       headers=_headers(acc))
    assert resp.status_code == 200
    body = resp.json()
    contents = [q["content"] for q in body["questions"]]
    assert "真题A" not in contents  # 排除原题
    assert all(q["source"] == "practice" for q in body["questions"])
    assert body["exam_id"] is not None
    exam = db.get(ExamRecord, body["exam_id"])
    assert exam.student_id == stu.id


def test_train_grades_and_syncs_review(exercise_client):
    client, db = exercise_client
    stu = _student(db)
    acc = _account(db, stu)
    exam = _practice_exam(db, stu, questions=[Q1, Q2])
    qids = [q.id for q in exam.questions]
    resp = client.post("/api/wrong-questions/train",
                       json={"student_id": stu.id, "answers": [
                           {"question_id": qids[0], "selected_option": "A"},  # Q1 answer A → 对
                           {"question_id": qids[1], "selected_option": "A"},  # Q2 answer B → 错
                       ]},
                       headers=_headers(acc))
    assert resp.status_code == 200
    body = resp.json()
    rec = db.get(ExamRecord, body["exam_id"])
    assert rec.student_id == stu.id
    assert rec.question_stats["mode"] == "training"
    assert set(rec.question_stats["question_ids"]) == set(qids)
    # 逐题批改返回判定
    assert [r["is_correct"] for r in body["results"]] == [True, False]
    # 批改写入 StudentAnswer
    answers = db.query(StudentAnswer).filter(StudentAnswer.exam_id == rec.id).all()
    assert len(answers) == 2
    assert all(a.answered_at is not None for a in answers)
    # 答错同步复习任务
    tasks = db.query(ReviewTask).filter_by(student_id=stu.id).all()
    assert [t.question_id for t in tasks] == [qids[1]]


def test_mastered_marks_done_and_removes(exercise_client):
    client, db = exercise_client
    stu = _student(db)
    acc = _account(db, stu)
    exam = _practice_exam(db, stu, questions=[Q1])
    q = exam.questions[0]
    _seed_answers(db, stu, exam, wrong_qids=[q.id])
    resp = client.post(f"/api/wrong-questions/{q.id}/mastered",
                       json={"student_id": stu.id}, headers=_headers(acc))
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "done"
    task = db.query(ReviewTask).filter_by(student_id=stu.id, question_id=q.id).first()
    assert task is not None and task.status == ReviewTaskStatus.done
    # 从错题列表移除
    assert client.get(f"/api/wrong-questions/{stu.id}", headers=_headers(acc)).json()["items"] == []


def test_mastered_not_own_403(exercise_client):
    client, db = exercise_client
    stu_a = _student(db, name="A")
    acc_a = _account(db, stu_a)
    stu_b = _student(db, name="B")
    exam_b = _practice_exam(db, stu_b, questions=[Q1])
    _seed_answers(db, stu_b, exam_b, wrong_qids=[exam_b.questions[0].id])
    resp = client.post(f"/api/wrong-questions/{exam_b.questions[0].id}/mastered",
                       json={"student_id": stu_b.id}, headers=_headers(acc_a))
    assert resp.status_code == 403


# ---------------- 跨会话持久化（生产等价：每请求独立会话） ----------------

@pytest.fixture()
def file_client(tmp_path):
    """文件库 + 每请求独立会话：请求结束未 commit 即回滚，可跨会话断言落库。"""
    engine = create_engine(f"sqlite:///{tmp_path / 'persist.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False,
                           expire_on_commit=False)

    def _get_db():
        s = factory()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _get_db
    with TestClient(app) as c:
        yield c, factory
    app.dependency_overrides.clear()
    engine.dispose()


def _seed_q_with_account(factory):
    db = factory()
    stu = _student(db)
    acc = _account(db, stu)
    q = Question(content="真题A", options=["A", "B"], answer="A",
                 knowledge_points="氧化还原反应", difficulty=Difficulty.easy,
                 source=QuestionSource.practice, audit_status=AuditStatus.passed,
                 audit_report={}, record_id=None)
    db.add(q)
    db.commit()
    db.close()
    return stu, acc, q


def test_train_persists_across_sessions(file_client):
    """训练会话的 ExamRecord / StudentAnswer / ReviewTask 在请求结束后仍落库。"""
    client, factory = file_client
    stu, acc, q = _seed_q_with_account(factory)
    resp = client.post("/api/wrong-questions/train",
                       json={"student_id": stu.id,
                             "answers": [{"question_id": q.id, "selected_option": "x"}]},
                       headers=_headers(acc))
    assert resp.status_code == 200
    assert resp.json()["results"][0]["is_correct"] is False
    db = factory()  # 全新会话：若请求未 commit，下面全查不到
    try:
        rec = db.query(ExamRecord).filter_by(student_id=stu.id).first()
        print(f"\n[DEBUG] fresh-session rec={rec} stuid={stu.id}")
        assert rec is not None and rec.question_stats["mode"] == "training"
        assert db.query(StudentAnswer).filter_by(
            student_id=stu.id, question_id=q.id, is_correct=False).count() == 1
        task = db.query(ReviewTask).filter_by(student_id=stu.id, question_id=q.id).first()
        assert task is not None and task.status == ReviewTaskStatus.pending
    finally:
        db.close()


def test_variants_persist_across_sessions(file_client, tmp_path):
    """变式题生成的 ExamRecord 与复制题目在请求结束后仍落库。"""
    _load_bank(tmp_path)
    client, factory = file_client
    stu, acc, original = _seed_q_with_account(factory)
    resp = client.post("/api/wrong-questions/variants",
                       json={"question_id": original.id, "count": 1},
                       headers=_headers(acc))
    assert resp.status_code == 200
    body = resp.json()
    assert body["question_count"] == 1
    variant_content = body["questions"][0]["content"]
    assert variant_content != original.content
    db = factory()
    try:
        rec = db.get(ExamRecord, body["exam_id"])
        assert rec is not None and rec.student_id == stu.id
        assert db.query(Question).filter(Question.content == variant_content).count() == 1
    finally:
        db.close()


def test_mastered_persists_across_sessions(file_client):
    """标记已掌握的 ReviewTask 置 done 在请求结束后仍落库（回归：缺 commit）。"""
    client, factory = file_client
    stu, acc, q = _seed_q_with_account(factory)
    # 先训练产生错题 → 生成 pending ReviewTask
    resp = client.post("/api/wrong-questions/train",
                       json={"student_id": stu.id,
                             "answers": [{"question_id": q.id, "selected_option": "x"}]},
                       headers=_headers(acc))
    assert resp.status_code == 200
    resp = client.post(f"/api/wrong-questions/{q.id}/mastered",
                       json={"student_id": stu.id}, headers=_headers(acc))
    assert resp.status_code == 200
    assert resp.json()["status"] == "done"
    db = factory()  # 全新会话：若 mastered 未 commit，done 状态查不到
    try:
        task = db.query(ReviewTask).filter_by(student_id=stu.id, question_id=q.id).first()
        assert task is not None and task.status == ReviewTaskStatus.done
    finally:
        db.close()
