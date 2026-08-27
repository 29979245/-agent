"""change parent-backend 2.8：周一周报 job 遍历范围。

断言：仅遍历有 active 绑定家长的学生；无绑定 / 仅 inactive 绑定的学生不被处理；
无数据学生不调用 LLM；单生失败不阻断其余。
"""
import datetime

import pytest

from app.db.models import (
    Class,
    ExamRecord,
    ExamStatus,
    ExamType,
    Grade,
    Parent,
    Question,
    School,
    Student,
    StudentAnswer,
    StudentParentBinding,
)
from app.db.models.enums import (
    AccountRole,
    Difficulty,
    ParentBindingRelation,
    ParentBindingStatus,
)
from app.services.exercise.scheduler import run_weekly_batch
from app.services.diagnosis.llm_diagnosis import DiagnosisLLMError

TODAY = datetime.date(2026, 8, 27)  # 周四（本周一 8/24 起）


def _at(day, hour=10):
    return datetime.datetime.combine(day, datetime.time(hour, 0))


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
    parent = Parent(name="张父", phone="13900000001")
    db_session.add(parent)
    db_session.flush()
    return {"class_id": cls.id, "parent_id": parent.id}


def _student(db_session, env, name):
    stu = Student(class_id=env["class_id"], name=name, bind_code="ABCDEF")
    db_session.add(stu)
    db_session.flush()
    return stu


def _bind(db_session, env, stu, status=ParentBindingStatus.active):
    db_session.add(
        StudentParentBinding(
            parent_id=env["parent_id"],
            student_id=stu.id,
            bind_code="ABCDEF",
            relation=ParentBindingRelation.father,
            status=status,
        )
    )
    db_session.flush()


def _give_answers(db_session, stu):
    q = Question(content="题目", answer="B", knowledge_points="氧化还原反应", difficulty=Difficulty.easy)
    db_session.add(q)
    db_session.flush()
    exam = ExamRecord(
        student_id=stu.id,
        name="每日练习",
        status=ExamStatus.published,
        exam_type=ExamType.practice,
        exam_date=TODAY,
        question_stats={"mode": "daily"},
    )
    db_session.add(exam)
    db_session.flush()
    db_session.add(
        StudentAnswer(
            student_id=stu.id,
            question_id=q.id,
            exam_id=exam.id,
            answer_text="B",
            is_correct=True,
            answered_at=_at(TODAY),
        )
    )
    db_session.flush()


class _MockClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def complete(self, messages):
        self.calls += 1
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


_VALID_JSON = (
    '{"summary": "本周完成了练习，大部分题目都做对了。", '
    '"detail": "最近在学习和氧气相关的反应。", '
    '"advice": "可以聊聊生活中的化学。", "no_data": false}'
)


def test_weekly_batch_only_bound_students(db_session, env):
    """仅遍历有 active 绑定的学生；无绑定 / 仅 inactive 绑定学生不被处理。"""
    bound = _student(db_session, env, "张三")
    _bind(db_session, env, bound)
    _give_answers(db_session, bound)

    unbound = _student(db_session, env, "王五")  # 无绑定
    _give_answers(db_session, unbound)

    inactive = _student(db_session, env, "赵六")  # 仅 inactive 绑定
    _bind(db_session, env, inactive, status=ParentBindingStatus.inactive)
    _give_answers(db_session, inactive)
    db_session.commit()

    client = _MockClient([_VALID_JSON])
    summary = run_weekly_batch(db_session, today=TODAY, client=client)
    assert summary["total"] == 1
    assert summary["generated"] == 1
    assert client.calls == 1  # 仅 bound 触发 LLM

    # bound 已缓存；unbound / inactive 未动
    db_session.refresh(bound)
    assert bound.weekly_report_week == datetime.date(2026, 8, 24)
    db_session.refresh(unbound)
    assert unbound.weekly_report_week is None
    db_session.refresh(inactive)
    assert inactive.weekly_report_week is None


def test_weekly_batch_no_data_does_not_call_llm(db_session, env):
    """active 绑定但本周无作答 → no_data 分支，不调用 LLM。"""
    bound = _student(db_session, env, "张三")
    _bind(db_session, env, bound)
    db_session.commit()
    client = _MockClient([])
    summary = run_weekly_batch(db_session, today=TODAY, client=client)
    assert summary["total"] == 1
    assert summary["generated"] == 0
    assert summary["no_data"] == 1
    assert client.calls == 0
    db_session.refresh(bound)
    assert bound.weekly_report_week == datetime.date(2026, 8, 24)


def test_weekly_batch_single_failure_does_not_block(db_session, env):
    """一个学生 LLM 失败计入 failed，其余学生正常生成。"""
    fail = _student(db_session, env, "李四")  # id 最小 → 先处理 → 失败
    _bind(db_session, env, fail)
    _give_answers(db_session, fail)
    ok = _student(db_session, env, "张三")
    _bind(db_session, env, ok)
    _give_answers(db_session, ok)
    db_session.commit()
    client = _MockClient([DiagnosisLLMError("boom"), _VALID_JSON])
    summary = run_weekly_batch(db_session, today=TODAY, client=client)
    assert summary["total"] == 2
    assert summary["generated"] == 1
    assert summary["failed"] == 1
    db_session.refresh(ok)
    assert ok.weekly_report_week == datetime.date(2026, 8, 24)
    db_session.refresh(fail)
    assert fail.weekly_report_week is None
