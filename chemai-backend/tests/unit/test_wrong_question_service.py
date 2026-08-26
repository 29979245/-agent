"""错题强化训练服务层单元测试（L1/L2）。

覆盖：错题列表去重/累计次数/已掌握移除、变式抽样排除原题与不足、训练会话 per-student
ExamRecord、标记已掌握终态与非本人 403。
"""
import datetime

import pytest

from app.core.exceptions import ForbiddenError, NotFoundError
from app.db.models import (
    Class,
    ExamRecord,
    Grade,
    Question,
    ReviewTask,
    School,
    Student,
    StudentAnswer,
)
from app.db.models.enums import Difficulty, ExamType, ReviewTaskStatus
from app.services.exercise.wrong_question import WrongQuestionTrainer


@pytest.fixture()
def env(db_session):
    school = School(name="S")
    db_session.add(school)
    db_session.flush()
    grade = Grade(school_id=school.id, name="高一", academic_year="2026")
    db_session.add(grade)
    db_session.flush()
    cls = Class(grade_id=grade.id, name="1班")
    db_session.add(cls)
    db_session.flush()
    exam = ExamRecord(class_id=None, student_id=None, name="base",
                      exam_type=ExamType.practice, exam_date=datetime.date.today())
    db_session.add(exam)
    db_session.flush()
    return {"class_id": cls.id, "exam_id": exam.id}


def _student(db_session, env, name="张三"):
    stu = Student(class_id=env["class_id"], name=name, barrier_profile={})
    db_session.add(stu)
    db_session.flush()
    return stu


def _question(db_session, content="Q", kp="氧化还原反应", difficulty=Difficulty.easy):
    q = Question(content=content, answer="a", options=["a", "b", "c"],
                 knowledge_points=kp, difficulty=difficulty)
    db_session.add(q)
    db_session.flush()
    return q


def _answer(db_session, env, stu, q, correct, when=None):
    db_session.add(StudentAnswer(
        student_id=stu.id, question_id=q.id, exam_id=env["exam_id"],
        answer_text="a", is_correct=correct,
        answered_at=when or datetime.datetime.utcnow(),
    ))
    db_session.flush()


def _load_bank(tmp_path, content):
    p = tmp_path / "全国卷" / "2024"
    p.mkdir(parents=True, exist_ok=True)
    (p / "真题.json").write_text(content, encoding="utf-8")
    from app.services.question.historical import reload_bank
    return reload_bank(tmp_path)


# ---- 4.1 错题列表 ----

def test_wrong_list_dedup_and_error_count(db_session, env):
    stu = _student(db_session, env)
    q1 = _question(db_session, content="Q1")
    q2 = _question(db_session, content="Q2", kp="化学平衡")
    _answer(db_session, env, stu, q1, correct=False, when=datetime.datetime(2026, 8, 1))
    _answer(db_session, env, stu, q1, correct=False, when=datetime.datetime(2026, 8, 2))
    _answer(db_session, env, stu, q1, correct=True, when=datetime.datetime(2026, 8, 3))  # 答对不算错题
    _answer(db_session, env, stu, q2, correct=False, when=datetime.datetime(2026, 8, 4))
    svc = WrongQuestionTrainer(db_session)
    items = svc.list_wrong_questions(stu.id)
    by_q = {i["question_id"]: i for i in items}
    assert set(by_q) == {q1.id, q2.id}  # 去重：q1 只出现一次
    assert by_q[q1.id]["error_count"] == 2  # 累计答错次数（答对不计）
    assert by_q[q2.id]["error_count"] == 1
    assert items[0]["question_id"] == q2.id  # 最近作答倒序
    assert items[0]["knowledge_points"] == ["化学平衡"]


def test_wrong_list_empty(db_session, env):
    stu = _student(db_session, env)
    assert WrongQuestionTrainer(db_session).list_wrong_questions(stu.id) == []


def test_wrong_list_excludes_mastered(db_session, env):
    stu = _student(db_session, env)
    q = _question(db_session)
    _answer(db_session, env, stu, q, correct=False)
    db_session.add(ReviewTask(student_id=stu.id, question_id=q.id,
                              status=ReviewTaskStatus.done))
    db_session.flush()
    assert WrongQuestionTrainer(db_session).list_wrong_questions(stu.id) == []


# ---- 4.2 变式题生成 ----

def test_variants_same_kp_diff_excludes_original(tmp_path, db_session, env):
    _load_bank(tmp_path, '{"questions": ['
               '{"id": "q1", "content": "真题A", "answer": "B", '
               '"knowledge_points": ["氧化还原反应"], "difficulty": "easy"},'
               '{"id": "q2", "content": "真题B", "answer": "C", '
               '"knowledge_points": ["氧化还原反应"], "difficulty": "easy"}'
               "]}")
    stu = _student(db_session, env)
    original = _question(db_session, content="真题A", kp="氧化还原反应", difficulty=Difficulty.easy)
    svc = WrongQuestionTrainer(db_session)
    result = svc.generate_variants(stu.id, original.id, count=2)
    assert result["question_count"] == 1
    assert result["shortfall"] == 1  # 仅命中 q2
    assert result["difficulty"] == "easy"
    assert result["questions"][0]["content"] == "真题B"  # 排除原题 q1
    assert all(q["source"] == "practice" for q in result["questions"])
    assert result["exam_id"] is not None
    exam = db_session.get(ExamRecord, result["exam_id"])
    assert exam.student_id == stu.id  # per-student ExamRecord
    assert exam.exam_type == ExamType.practice


def test_variants_unknown_question_404(db_session, env):
    svc = WrongQuestionTrainer(db_session)
    with pytest.raises(NotFoundError):
        svc.generate_variants(_student(db_session, env).id, 99999)


# ---- 4.3 训练会话 ----

def test_train_grades_and_syncs_review(db_session, env):
    stu = _student(db_session, env)
    q1 = _question(db_session, content="Q1")  # answer="a"
    q2 = _question(db_session, content="Q2")
    svc = WrongQuestionTrainer(db_session)
    result = svc.start_training(stu.id, [
        {"question_id": q1.id, "selected_option": "a"},  # 答对
        {"question_id": q2.id, "selected_option": "x"},  # 答错
    ])
    assert result["question_count"] == 2
    assert [r["is_correct"] for r in result["results"]] == [True, False]
    exam = db_session.get(ExamRecord, result["exam_id"])
    assert exam.student_id == stu.id
    assert exam.question_stats["mode"] == "training"
    assert set(exam.question_stats["question_ids"]) == {q1.id, q2.id}
    # 不复制题目，保持原题 identity
    assert db_session.query(Question).filter(Question.record_id == exam.id).count() == 0
    # 逐题批改写入 StudentAnswer（含 answered_at）
    answers = db_session.query(StudentAnswer).filter(StudentAnswer.exam_id == exam.id).all()
    assert len(answers) == 2
    assert all(a.answered_at is not None for a in answers)
    by_q = {a.question_id: a for a in answers}
    assert by_q[q1.id].is_correct is True
    assert by_q[q2.id].is_correct is False
    # 答错触发复习任务同步
    tasks = db_session.query(ReviewTask).filter_by(student_id=stu.id).all()
    assert [t.question_id for t in tasks] == [q2.id]


def test_train_review_sync_dedup(db_session, env):
    """同题训练答错复用既有 ReviewTask，不重复创建。"""
    stu = _student(db_session, env)
    q = _question(db_session)
    db_session.add(ReviewTask(student_id=stu.id, question_id=q.id,
                              status=ReviewTaskStatus.pending))
    db_session.flush()
    WrongQuestionTrainer(db_session).start_training(
        stu.id, [{"question_id": q.id, "selected_option": "x"}]
    )
    assert db_session.query(ReviewTask).filter_by(student_id=stu.id).count() == 1


def test_train_missing_question_404(db_session, env):
    with pytest.raises(NotFoundError):
        WrongQuestionTrainer(db_session).start_training(
            _student(db_session, env).id, [{"question_id": 99999, "selected_option": "a"}]
        )


# ---- 4.4 标记已掌握 ----

def test_mastered_marks_review_done(db_session, env):
    stu = _student(db_session, env)
    q = _question(db_session)
    _answer(db_session, env, stu, q, correct=False)
    task = ReviewTask(student_id=stu.id, question_id=q.id)
    db_session.add(task)
    db_session.flush()
    result = WrongQuestionTrainer(db_session).mark_mastered(stu.id, q.id)
    assert result["status"] == "done"
    assert result["review_level"] == "level6"
    db_session.refresh(task)
    assert task.status == ReviewTaskStatus.done
    assert task.next_review_at is None
    assert task.completed_at is not None
    # 从错题列表移除
    assert WrongQuestionTrainer(db_session).list_wrong_questions(stu.id) == []


def test_mastered_creates_task_when_missing(db_session, env):
    stu = _student(db_session, env)
    q = _question(db_session)
    _answer(db_session, env, stu, q, correct=False)
    result = WrongQuestionTrainer(db_session).mark_mastered(stu.id, q.id)
    assert result["review_task_id"] is not None
    assert db_session.query(ReviewTask).filter_by(student_id=stu.id, question_id=q.id).count() == 1


def test_mastered_not_own_403(db_session, env):
    owner = _student(db_session, env, name="A")
    other = _student(db_session, env, name="B")
    q = _question(db_session)
    _answer(db_session, env, owner, q, correct=False)
    with pytest.raises(ForbiddenError):
        WrongQuestionTrainer(db_session).mark_mastered(other.id, q.id)
