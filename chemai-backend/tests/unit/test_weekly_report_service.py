"""change parent-backend 2.4：周报生成服务。

覆盖：JSON 解析 / 无效响应纠错 / no_data 分支 / 降级错误信号。
"""
import datetime

import pytest

from app.db.models import (
    Class,
    ExamRecord,
    ExamStatus,
    ExamType,
    Grade,
    Question,
    School,
    Student,
    StudentAnswer,
)
from app.db.models.enums import Difficulty
from app.services.analytics.weekly_report_service import (
    WeeklyReportError,
    generate_ai_summary,
    generate_weekly_report,
    parse_weekly_report_response,
)
from app.services.diagnosis.llm_diagnosis import DiagnosisLLMError

TODAY = datetime.date(2026, 8, 27)


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
    stu = Student(class_id=cls.id, name="张三", bind_code="ABCDEF")
    db_session.add(stu)
    db_session.flush()
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
    db_session.commit()
    return {"student_id": stu.id}


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
    '"detail": "最近在学习和氧气相关的反应，理解不错。", '
    '"advice": "可以和孩子聊聊生活中的化学现象。", "no_data": false}'
)


# ---------------- JSON 解析 ----------------

def test_parse_valid_json():
    result = parse_weekly_report_response(_VALID_JSON)
    assert result["summary"].startswith("本周完成了练习")
    assert result["no_data"] is False


def test_parse_missing_summary_raises():
    with pytest.raises(ValueError):
        parse_weekly_report_response('{"detail": "x", "no_data": false}')


# ---------------- 成功生成 ----------------

def test_generate_success(db_session, env):
    client = _MockClient([_VALID_JSON])
    report = generate_weekly_report(db_session, env["student_id"], today=TODAY, client=client)
    assert report["no_data"] is False
    assert report["summary"].startswith("本周完成了练习")


def test_generate_invalid_then_corrective(db_session, env):
    # 首次返回垃圾，纠错后返回合法 JSON → 成功（客户端被调用 2 次）
    client = _MockClient(["这不是 JSON", _VALID_JSON])
    report = generate_weekly_report(db_session, env["student_id"], today=TODAY, client=client)
    assert client.calls == 2
    assert report["no_data"] is False


def test_generate_exhausted_raises(db_session, env):
    # 全部解析失败 → 纠错重试耗尽 → 降级错误信号（抛 WeeklyReportError）
    client = _MockClient(["垃圾", "还是垃圾", "仍旧垃圾"])
    with pytest.raises(WeeklyReportError):
        generate_weekly_report(db_session, env["student_id"], today=TODAY, client=client)
    assert client.calls == 3


def test_generate_llm_failure_raises(db_session, env):
    client = _MockClient([DiagnosisLLMError("boom")])
    with pytest.raises(WeeklyReportError):
        generate_weekly_report(db_session, env["student_id"], today=TODAY, client=client)


# ---------------- no_data 分支 ----------------

def test_generate_no_data_without_calling_llm(db_session, env):
    stu = db_session.get(Student, env["student_id"])
    # 清空本周作答 → 无数据
    db_session.query(StudentAnswer).filter(StudentAnswer.student_id == stu.id).delete()
    db_session.commit()
    class _Noop:
        def complete(self, messages):
            raise AssertionError("无数据分支不应调用 LLM")

    report = generate_weekly_report(db_session, env["student_id"], today=TODAY, client=_Noop())
    assert report["no_data"] is True
    assert report["summary"] == "本周暂无练习记录"
    assert report["detail"] == "" and report["advice"] == ""


# ---------------- ai-summary ----------------

def test_ai_summary_success(db_session, env):
    client = _MockClient(['{"summary": "孩子最近状态不错，继续加油。", "no_data": false}'])
    result = generate_ai_summary(
        db_session,
        env["student_id"],
        report={"stats": {"weekly_exercises": 1, "weekly_accuracy": 1.0, "streak_days": 1, "total_practices": 1}, "knowledge_points": []},
        today=TODAY,
        client=client,
    )
    assert result["summary"] == "孩子最近状态不错，继续加油。"


def test_ai_summary_failure_raises(db_session, env):
    client = _MockClient([DiagnosisLLMError("boom")])
    with pytest.raises(WeeklyReportError):
        generate_ai_summary(
            db_session,
            env["student_id"],
            report={"stats": {}, "knowledge_points": []},
            today=TODAY,
            client=client,
        )
