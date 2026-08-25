"""考试生命周期服务测试（4.2-4.3）：六态流转、双渠道关联、发布统计、级联删除、结果查询。"""
import datetime

import pytest
from fastapi import HTTPException

from app.db.models import Class, ExamRecord, Grade, Question, School, Student, StudentAnswer
from app.db.models.enums import Difficulty, ExamStatus, ExamType
from app.services.question.exam_service import ExamService
from app.services.question.historical import HistoricalBank


def _org(db_session):
    school = School(name="S")
    db_session.add(school)
    db_session.flush()
    grade = Grade(school_id=school.id, name="高一", academic_year="2026")
    db_session.add(grade)
    db_session.flush()
    cls = Class(grade_id=grade.id, name="1班")
    db_session.add(cls)
    db_session.flush()
    return cls


def _exam(db_session, cls, **kw):
    svc = ExamService(db_session)
    return svc.create(
        class_id=cls.id,
        name=kw.get("name", "期中考试"),
        exam_date=kw.get("exam_date", datetime.date(2026, 8, 25)),
        exam_type=kw.get("exam_type", "exam"),
    )


def _question(db_session, **kw):
    q = Question(
        content=kw.get("content", "题A"),
        answer=kw.get("answer", "a"),
        difficulty=Difficulty.easy,
    )
    db_session.add(q)
    db_session.flush()
    return q


def _bank(tmp_path):
    p = tmp_path / "全国卷" / "2024"
    p.mkdir(parents=True, exist_ok=True)
    (p / "高考化学.json").write_text(
        '{"title": "高考化学", "questions": ['
        '{"id": "q1", "content": "真题：氧化还原", "answer": "B",'
        ' "knowledge_points": ["氧化还原"], "difficulty": "medium"}]}',
        encoding="utf-8",
    )
    return HistoricalBank().load_from(tmp_path)


# ---- 创建 ----

def test_create_exam_draft(db_session):
    cls = _org(db_session)
    exam = _exam(db_session, cls, name="期中")
    assert exam.status == ExamStatus.draft
    assert exam.name == "期中"
    assert exam.exam_type == ExamType.exam


# ---- 4.3 双渠道关联 ----

def test_add_questions_channel1_direct(db_session):
    cls = _org(db_session)
    exam = _exam(db_session, cls)
    q = _question(db_session)
    svc = ExamService(db_session)
    res = svc.add_questions(exam.id, [q.id])
    db_session.commit()
    assert res == {"added": 1, "skipped": 0, "skipped_reasons": {}}
    got = db_session.get(Question, q.id)
    assert got.record_id == exam.id
    assert len(svc.list_questions(exam.id)) == 1


def test_add_questions_channel2_historical(db_session, tmp_path):
    cls = _org(db_session)
    exam = _exam(db_session, cls)
    svc = ExamService(db_session, bank=_bank(tmp_path))
    res = svc.add_questions(exam.id, ["全国卷/2024/高考化学#q1"])
    db_session.commit()
    assert res == {"added": 1, "skipped": 0, "skipped_reasons": {}}
    items = svc.list_questions(exam.id)
    assert len(items) == 1
    assert items[0]["content"] == "真题：氧化还原"
    # 渠道二复制出 Question 实体且已关联
    copied = db_session.query(Question).filter_by(record_id=exam.id).first()
    assert copied is not None
    assert copied.content == "真题：氧化还原"


def test_add_questions_skips_duplicate_and_missing(db_session, tmp_path):
    cls = _org(db_session)
    exam = _exam(db_session, cls)
    q = _question(db_session)
    svc = ExamService(db_session, bank=_bank(tmp_path))
    svc.add_questions(exam.id, [q.id])
    res = svc.add_questions(exam.id, [q.id, 9999, "全国卷/2024/高考化学#nope"])
    assert res["added"] == 0 and res["skipped"] == 3
    assert res["skipped_reasons"] == {
        str(q.id): "已在本考试中",
        "9999": "题目不存在",
        "全国卷/2024/高考化学#nope": "真题未命中",
    }


def test_add_questions_skip_claimed_by_other_exam(db_session):
    """渠道一：题目已被其他考试占用时跳过，并给出占用考试名。"""
    cls = _org(db_session)
    exam_a = _exam(db_session, cls, name="期末")
    exam_b = _exam(db_session, cls, name="期中")
    q = _question(db_session)
    svc = ExamService(db_session)
    svc.add_questions(exam_a.id, [q.id])
    db_session.commit()
    res = svc.add_questions(exam_b.id, [q.id])
    assert res["added"] == 0 and res["skipped"] == 1
    assert res["skipped_reasons"] == {str(q.id): "已被考试「期末」占用"}


def test_add_questions_channel2_dedup(db_session, tmp_path):
    """渠道二去重：同一 ref_id 同批/跨批重复关联只复制一次。"""
    cls = _org(db_session)
    exam = _exam(db_session, cls)
    svc = ExamService(db_session, bank=_bank(tmp_path))
    ref = "全国卷/2024/高考化学#q1"
    # 同批重复
    res = svc.add_questions(exam.id, [ref, ref])
    db_session.commit()
    assert res["added"] == 1 and res["skipped"] == 1
    assert res["skipped_reasons"] == {ref: "同批重复，已复制过"}
    # 跨批重复
    res2 = svc.add_questions(exam.id, [ref])
    db_session.commit()
    assert res2["added"] == 0 and res2["skipped"] == 1
    assert res2["skipped_reasons"] == {ref: "该真题已在本考试中"}
    # 仅复制出一份 Question 实体
    copied = db_session.query(Question).filter_by(record_id=exam.id).all()
    assert len(copied) == 1


def test_add_questions_rejected_after_draft(db_session):
    cls = _org(db_session)
    exam = _exam(db_session, cls)
    q = _question(db_session)
    svc = ExamService(db_session)
    svc.add_questions(exam.id, [q.id])
    svc.publish(exam.id)
    with pytest.raises(HTTPException) as exc:
        svc.add_questions(exam.id, [_question(db_session).id])
    assert exc.value.status_code == 400


def test_remove_question_unlinks(db_session):
    cls = _org(db_session)
    exam = _exam(db_session, cls)
    q = _question(db_session)
    svc = ExamService(db_session)
    svc.add_questions(exam.id, [q.id])
    svc.remove_question(exam.id, q.id)
    db_session.commit()
    assert db_session.get(Question, q.id).record_id is None
    assert svc.list_questions(exam.id) == []


# ---- 发布 ----

def test_publish_empty_rejected(db_session):
    cls = _org(db_session)
    exam = _exam(db_session, cls)
    svc = ExamService(db_session)
    with pytest.raises(HTTPException) as exc:
        svc.publish(exam.id)
    assert exc.value.status_code == 400


def test_publish_writes_question_stats(db_session):
    cls = _org(db_session)
    exam = _exam(db_session, cls)
    q = _question(db_session)
    stu = Student(class_id=cls.id, name="张三")
    db_session.add(stu)
    db_session.flush()
    svc = ExamService(db_session)
    svc.add_questions(exam.id, [q.id])
    svc.publish(exam.id)
    db_session.commit()
    assert exam.status == ExamStatus.published
    qs = exam.question_stats
    assert qs["published"] is True
    assert qs["question_count"] == 1
    assert qs["total_students"] == 1
    assert "published_at" in qs


# ---- 状态流转 ----

def test_full_lifecycle_flow(db_session):
    cls = _org(db_session)
    exam = _exam(db_session, cls)
    q = _question(db_session)
    svc = ExamService(db_session)
    svc.add_questions(exam.id, [q.id])
    svc.publish(exam.id)
    assert exam.status == ExamStatus.published
    svc.start_grading(exam.id)   # published → in_progress → grading
    assert exam.status == ExamStatus.grading
    svc.finalize(exam.id)
    assert exam.status == ExamStatus.completed
    assert exam.attendee_count == 0
    assert "total_students" in exam.stats
    svc.archive(exam.id)
    assert exam.status == ExamStatus.archived


def test_illegal_skip_grading_archived(db_session):
    """跳过阅卷直接归档被拦截（4.5 非法转换 400）。"""
    cls = _org(db_session)
    exam = _exam(db_session, cls)
    svc = ExamService(db_session)
    with pytest.raises(HTTPException) as exc:
        svc.archive(exam.id)  # draft 直接归档
    assert exc.value.status_code == 400
    assert exam.status == ExamStatus.draft


def test_archived_terminal_readonly(db_session):
    cls = _org(db_session)
    exam = _exam(db_session, cls)
    svc = ExamService(db_session)
    exam.status = ExamStatus.archived
    db_session.flush()
    with pytest.raises(HTTPException):
        svc.publish(exam.id)
    with pytest.raises(HTTPException):
        svc.delete(exam.id)


# ---- 级联删除 ----

def test_delete_draft_cascades(db_session):
    cls = _org(db_session)
    exam = _exam(db_session, cls)
    q = _question(db_session)
    stu = Student(class_id=cls.id, name="张三")
    db_session.add(stu)
    db_session.flush()
    ans = StudentAnswer(student_id=stu.id, question_id=q.id, exam_id=exam.id, answer_text="a", is_correct=True)
    db_session.add(ans)
    db_session.flush()
    exam_id, ans_id = exam.id, ans.id
    db_session.commit()
    svc = ExamService(db_session)
    svc.delete(exam_id)
    db_session.commit()
    assert db_session.get(ExamRecord, exam_id) is None
    assert db_session.get(StudentAnswer, ans_id) is None
    # 关联题目 record_id 置空，题目实体保留
    assert db_session.get(Question, q.id) is not None
    assert db_session.get(Question, q.id).record_id is None


def test_delete_published_rejected(db_session):
    cls = _org(db_session)
    exam = _exam(db_session, cls)
    q = _question(db_session)
    svc = ExamService(db_session)
    svc.add_questions(exam.id, [q.id])
    svc.publish(exam.id)
    exam_id = exam.id
    with pytest.raises(HTTPException):
        svc.delete(exam_id)
    assert db_session.get(ExamRecord, exam_id) is not None


# ---- 结果查询 ----

def test_results_overview_and_student_detail(db_session):
    cls = _org(db_session)
    exam = _exam(db_session, cls)
    q1 = _question(db_session, content="题1", answer="a")
    q2 = _question(db_session, content="题2", answer="b")
    svc = ExamService(db_session)
    svc.add_questions(exam.id, [q1.id, q2.id])
    stu1 = Student(class_id=cls.id, name="张三")
    stu2 = Student(class_id=cls.id, name="李四")
    db_session.add_all([stu1, stu2])
    db_session.flush()
    db_session.add_all([
        StudentAnswer(student_id=stu1.id, question_id=q1.id, exam_id=exam.id, is_correct=True),
        StudentAnswer(student_id=stu1.id, question_id=q2.id, exam_id=exam.id, is_correct=False),
        StudentAnswer(student_id=stu2.id, question_id=q1.id, exam_id=exam.id, is_correct=False),
    ])
    db_session.commit()
    exam.status = ExamStatus.grading
    db_session.flush()
    svc.finalize(exam.id)
    assert exam.attendee_count == 2

    ov = svc.results(exam.id)
    assert ov["total_students"] == 2
    assert ov["question_count"] == 2
    by_name = {s["name"]: s for s in ov["students"]}
    assert by_name["张三"]["correct_count"] == 1
    assert ov["avg_score"] == 0.25  # (0.5 + 0.0) / 2

    det = svc.student_result(exam.id, stu1.id)
    assert len(det["answers"]) == 2
    assert det["answers"][0]["is_correct"] is True
