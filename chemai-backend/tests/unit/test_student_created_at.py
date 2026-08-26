"""Student.created_at 字段与迁移回填语义测试（1.2）。

回填 SQL 与迁移文件保持一致：COALESCE(max(answered_at), CURRENT_TIMESTAMP)。
存量行（列缺失时插入 → created_at NULL）是回填对象；新插入行由 default=utcnow 自动赋值。
"""
import datetime

from sqlalchemy import text

from app.db.models import Class, ExamRecord, Grade, Question, School, Student, StudentAnswer
from app.db.models.enums import Difficulty, ExamStatus, ExamType

BACKFILL_SQL = """
UPDATE student
SET created_at = COALESCE(
    (SELECT MAX(sa.answered_at) FROM student_answer sa WHERE sa.student_id = student.id),
    CURRENT_TIMESTAMP
)
WHERE created_at IS NULL
"""


def _student(db_session, **kw):
    school = School(name="S")
    db_session.add(school)
    db_session.flush()
    grade = Grade(school_id=school.id, name="高一", academic_year="2026")
    db_session.add(grade)
    db_session.flush()
    cls = Class(grade_id=grade.id, name="1班")
    db_session.add(cls)
    db_session.flush()
    stu = Student(class_id=cls.id, name="张三", **kw)
    db_session.add(stu)
    db_session.flush()
    return stu, cls


def _as_legacy_null(db_session, student_id: int) -> None:
    """模拟存量行：列缺失时插入 → created_at 为 NULL（绕过 ORM insert 默认）。"""
    db_session.execute(
        text("UPDATE student SET created_at = NULL WHERE id = :sid"),
        {"sid": student_id},
    )
    db_session.commit()


def _answer(db_session, student, cls, answered_at):
    q = Question(content="c", answer="a", difficulty=Difficulty.easy)
    db_session.add(q)
    db_session.flush()
    exam = ExamRecord(
        student_id=student.id, name="练习", exam_type=ExamType.practice,
        status=ExamStatus.published, exam_date=datetime.date(2026, 8, 26),
    )
    db_session.add(exam)
    db_session.flush()
    db_session.add(
        StudentAnswer(
            student_id=student.id, question_id=q.id, exam_id=exam.id,
            answered_at=answered_at,
        )
    )
    db_session.flush()


def test_created_at_defaults_on_insert(db_session):
    """新学生插入自动落 utcnow（default=utcnow）。"""
    stu, _ = _student(db_session)
    db_session.commit()
    got = db_session.get(Student, stu.id)
    assert got.created_at is not None
    assert isinstance(got.created_at, datetime.datetime)


def test_created_at_column_accepts_null(db_session):
    """NULL 兜底：数据库列可空，防御性 NULL 可落库（检测层 2.1 跳过）。"""
    stu, _ = _student(db_session)
    _as_legacy_null(db_session, stu.id)
    got = db_session.get(Student, stu.id)
    assert got.created_at is None


def test_backfill_uses_last_answer(db_session):
    """有作答的存量学生（created_at NULL）回填到最近作答时间。"""
    stu, cls = _student(db_session)
    _answer(db_session, stu, cls, datetime.datetime(2026, 8, 20, 10, 0, 0))
    _answer(db_session, stu, cls, datetime.datetime(2026, 8, 25, 9, 30, 0))
    _as_legacy_null(db_session, stu.id)
    db_session.execute(text(BACKFILL_SQL))
    db_session.commit()
    got = db_session.get(Student, stu.id)
    assert got.created_at is not None
    assert got.created_at.date() == datetime.date(2026, 8, 25)


def test_backfill_no_answers_uses_now(db_session):
    """从未作答的存量学生回填到迁移时刻（非空、接近 now）。"""
    stu, _ = _student(db_session)
    _as_legacy_null(db_session, stu.id)
    before = datetime.datetime.utcnow()
    db_session.execute(text(BACKFILL_SQL))
    db_session.commit()
    got = db_session.get(Student, stu.id)
    assert got.created_at is not None
    assert before - datetime.timedelta(minutes=1) <= got.created_at <= before + datetime.timedelta(minutes=1)
