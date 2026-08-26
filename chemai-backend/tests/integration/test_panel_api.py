"""学情面板 API L2 集成测试（class-learning-panel，task 2.1-3.1/4.1-4.2）。

覆盖：面板完整数据/空数据、知识点下钻、学生详情 404、趋势、教师首页概览、
PDF 导出、班级学生列表、学生角色 403、教师跨校隔离。
"""
import datetime

from app.core.security import create_token
from app.db.models import (
    Account,
    Class,
    ExamRecord,
    Grade,
    Question,
    School,
    Student,
    StudentAnswer,
    Teacher,
)
from app.db.models.enums import (
    AccountRole,
    AuditStatus,
    Difficulty,
    ExamStatus,
    ExamType,
    QuestionSource,
)
from app.services.analytics.panel_service import load_class_panel, panel_report_html
from tests.integration.conftest import exercise_client  # noqa: F401

_TODAY = datetime.date.today()


def _school(db, name="S1"):
    s = School(name=name, current_semester="2026-1")
    db.add(s)
    db.flush()
    return s


def _grade(db, school, name="高一"):
    g = Grade(school_id=school.id, name=name, academic_year="2026")
    db.add(g)
    db.flush()
    return g


def _class(db, grade, name="1班"):
    c = Class(grade_id=grade.id, name=name)
    db.add(c)
    db.flush()
    return c


def _student(db, cls, name, barrier=None):
    s = Student(class_id=cls.id, name=name, barrier_profile=barrier or {})
    db.add(s)
    db.flush()
    return s


def _teacher(db, school, name="王老师"):
    t = Teacher(school_id=school.id, name=name, phone=f"13{name.encode().hex()[:8]}")
    db.add(t)
    db.flush()
    acc = Account(username=f"t{name}", password_hash="x", role=AccountRole.teacher,
                  role_id=t.id)
    db.add(acc)
    db.flush()
    return t, acc


def _auth(account, role="teacher", school_id=1):
    token = create_token(user_id=account.id, role=role, school_id=school_id)
    return {"Authorization": f"Bearer {token}"}


def _exam(db, cls, name, exam_date, status=ExamStatus.completed):
    e = ExamRecord(class_id=cls.id, name=name, exam_type=ExamType.exam,
                   status=status, exam_date=exam_date)
    db.add(e)
    db.flush()
    return e


def _question(db, kp="氧化还原"):
    q = Question(content="题", options=["A"], answer="A", analysis="", knowledge_points=kp,
                 difficulty=Difficulty.medium, source=QuestionSource.ai,
                 audit_status=AuditStatus.passed, audit_report={})
    db.add(q)
    db.flush()
    return q


def _answer(db, student, question, exam, is_correct=True, answered_at=None):
    a = StudentAnswer(student_id=student.id, question_id=question.id, exam_id=exam.id,
                      answer_text="A", is_correct=is_correct,
                      answered_at=answered_at or datetime.datetime.utcnow())
    db.add(a)
    db.flush()
    return a


def _setup_class_data(db, school=None):
    """学校 A：班级 + 教师 + 两场考试数据；返回上下文对象。"""
    school = school or _school(db, "A校")
    grade = _grade(db, school)
    cls = _class(db, grade, "1班")
    s1 = _student(db, cls, "张三", {"concept": 0.6, "reading": 0.2, "expression": 0.2})
    s2 = _student(db, cls, "李四", {"concept": 0.0, "reading": 1.0, "expression": 0.0})
    teacher, tacc = _teacher(db, school, "王老师")
    exam1 = _exam(db, cls, "期中", _TODAY - datetime.timedelta(days=20))
    exam2 = _exam(db, cls, "期末", _TODAY - datetime.timedelta(days=5))
    q1 = _question(db, "氧化还原")
    q2 = _question(db, "离子反应")
    _answer(db, s1, q1, exam1, is_correct=False)
    _answer(db, s2, q1, exam1, is_correct=True)
    _answer(db, s1, q2, exam2, is_correct=True)
    _answer(db, s2, q2, exam2, is_correct=True)
    db.commit()
    return _OrgCtx(school, grade, cls, s1, s2, teacher, tacc, exam1, exam2)


class _OrgCtx:
    def __init__(self, school, grade, cls, s1, s2, teacher, tacc, exam1, exam2):
        self.school = school
        self.grade = grade
        self.cls = cls
        self.s1 = s1
        self.s2 = s2
        self.teacher = teacher
        self.tacc = tacc
        self.exam1 = exam1
        self.exam2 = exam2


# ---------------- 2.1 班级学情面板 ----------------

def test_class_panel_full_data(exercise_client):
    client, db = exercise_client
    ctx = _setup_class_data(db)
    resp = client.get(f"/api/panel/class/{ctx.cls.id}", headers=_auth(ctx.tacc))
    assert resp.status_code == 200
    body = resp.json()
    ov = body["class_overview"]
    assert ov["class_id"] == ctx.cls.id
    assert ov["total_students"] == 2
    assert ov["exam_count"] == 2
    assert [p["exam_date"] for p in ov["avg_score_trend"]] == sorted(
        p["exam_date"] for p in ov["avg_score_trend"]
    )
    assert ov["recent_exam_avg"] is not None
    assert 0 <= ov["recent_exam_avg"] <= 100
    # 知识点错误率降序：氧化还原 50% 在离子反应 0% 前
    kps = body["knowledge_points"]
    assert [k["knowledge_point"] for k in kps] == ["氧化还原", "离子反应"]
    assert kps[0]["error_rate"] == 50.0
    assert kps[1]["error_rate"] == 0.0
    # top_errors 是 top5 截断
    assert body["top_errors"] == kps[:5]
    # 障碍主导计数（整数人数）
    assert body["barrier_distribution"] == {"concept": 1, "reading": 1, "expression": 0}


def test_class_panel_empty_data(exercise_client):
    client, db = exercise_client
    school = _school(db)
    grade = _grade(db, school)
    cls = _class(db, grade, "空班")
    _student(db, cls, "仅一名")
    teacher, tacc = _teacher(db, school, "陈老师")
    db.commit()
    resp = client.get(f"/api/panel/class/{cls.id}", headers=_auth(tacc))
    assert resp.status_code == 200
    ov = resp.json()["class_overview"]
    assert ov["exam_count"] == 0
    assert ov["avg_score_trend"] == []
    assert ov["recent_exam_avg"] is None
    assert ov["recent_exam_date"] is None
    assert resp.json()["knowledge_points"] == []
    assert resp.json()["top_errors"] == []
    assert resp.json()["barrier_distribution"] == {"concept": 0, "reading": 0, "expression": 0}


def test_class_panel_practice_not_counted(exercise_client):
    """D3：仅 exam 计入均分，practice 排除。"""
    client, db = exercise_client
    ctx = _setup_class_data(db)
    # 追加 per-student 练习（exam_type=practice），不应影响 exam_count
    p = ExamRecord(student_id=ctx.s1.id, class_id=None, name="每日练习",
                   exam_type=ExamType.practice, status=ExamStatus.published,
                   exam_date=_TODAY)
    db.add(p)
    db.commit()
    resp = client.get(f"/api/panel/class/{ctx.cls.id}", headers=_auth(ctx.tacc))
    assert resp.json()["class_overview"]["exam_count"] == 2


# ---------------- 2.2 按知识点查看 ----------------

def test_knowledge_detail(exercise_client):
    client, db = exercise_client
    ctx = _setup_class_data(db)
    resp = client.get(
        f"/api/panel/class/{ctx.cls.id}/knowledge/氧化还原", headers=_auth(ctx.tacc)
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert body["error_count"] == 1
    assert body["error_rate"] == 50.0
    assert body["students"] == [
        {"student_id": ctx.s1.id, "name": "张三", "wrong_count": 1}
    ]


def test_knowledge_detail_unknown_kp_zero(exercise_client):
    client, db = exercise_client
    ctx = _setup_class_data(db)
    resp = client.get(
        f"/api/panel/class/{ctx.cls.id}/knowledge/未考知识点", headers=_auth(ctx.tacc)
    )
    assert resp.status_code == 200
    assert resp.json()["total"] == 0
    assert resp.json()["error_rate"] == 0.0
    assert resp.json()["students"] == []


# ---------------- 2.3 按学生查看 ----------------

def test_student_detail(exercise_client):
    client, db = exercise_client
    ctx = _setup_class_data(db)
    resp = client.get(
        f"/api/panel/class/{ctx.cls.id}/student/{ctx.s1.id}", headers=_auth(ctx.tacc)
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "张三"
    assert body["barrier_profile"] == {"concept": 0.6, "reading": 0.2, "expression": 0.2}
    assert body["weak_knowledge_points"] == ["氧化还原"]  # 仅错题知识点
    assert len(body["wrong_history"]) == 1


def test_student_detail_cross_class_404(exercise_client):
    client, db = exercise_client
    ctx = _setup_class_data(db)
    other = _student(db, _class(db, _grade(db, ctx.school), "2班"), "外人")
    db.commit()
    resp = client.get(
        f"/api/panel/class/{ctx.cls.id}/student/{other.id}", headers=_auth(ctx.tacc)
    )
    assert resp.status_code == 404


def test_student_detail_unknown_404(exercise_client):
    client, db = exercise_client
    ctx = _setup_class_data(db)
    resp = client.get(
        f"/api/panel/class/{ctx.cls.id}/student/99999", headers=_auth(ctx.tacc)
    )
    assert resp.status_code == 404


# ---------------- 2.4 趋势 ----------------

def test_trend_has_data(exercise_client):
    client, db = exercise_client
    ctx = _setup_class_data(db)
    resp = client.get(f"/api/panel/class/{ctx.cls.id}/trend", headers=_auth(ctx.tacc))
    assert resp.status_code == 200
    trend = resp.json()["trend"]
    assert [p["exam_date"] for p in trend] == sorted(p["exam_date"] for p in trend)
    assert len(trend) == 2


def test_trend_empty_array(exercise_client):
    client, db = exercise_client
    school = _school(db)
    cls = _class(db, _grade(db, school), "空班")
    teacher, tacc = _teacher(db, school, "陈老师")
    db.commit()
    resp = client.get(f"/api/panel/class/{cls.id}/trend", headers=_auth(tacc))
    assert resp.status_code == 200
    assert resp.json()["trend"] == []


# ---------------- 2.5 教师首页概览 ----------------

def test_dashboard_own_classes(exercise_client):
    client, db = exercise_client
    ctx = _setup_class_data(db)
    resp = client.get(
        f"/api/panel/dashboard/{ctx.teacher.id}", headers=_auth(ctx.tacc)
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["teacher_id"] == ctx.teacher.id
    assert len(body["classes"]) == 1
    item = body["classes"][0]
    assert item["class_id"] == ctx.cls.id
    assert item["total_students"] == 2
    assert item["recent_exam_avg"] is not None
    assert item["barrier_distribution"] == {"concept": 1, "reading": 1, "expression": 0}
    assert item["practice_count"] == 0


def test_dashboard_cross_teacher_403(exercise_client):
    client, db = exercise_client
    ctx = _setup_class_data(db)
    school_b = _school(db, "B校")
    other_t, other_acc = _teacher(db, school_b, "赵老师")
    db.commit()
    # 教师 B 请求教师 A 的概览 → 403（仅本人/admin+）
    resp = client.get(
        f"/api/panel/dashboard/{ctx.teacher.id}", headers=_auth(other_acc)
    )
    assert resp.status_code == 403


# ---------------- 2.6 PDF 导出 ----------------

def test_export_panel_pdf(exercise_client):
    client, db = exercise_client
    ctx = _setup_class_data(db)
    resp = client.get(f"/api/panel/export/{ctx.cls.id}", headers=_auth(ctx.tacc))
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content.startswith(b"%PDF")
    assert len(resp.content) > 0


def test_export_excludes_other_class_data(exercise_client):
    client, db = exercise_client
    ctx = _setup_class_data(db)
    other_cls = _class(db, _grade(db, ctx.school), "别班")
    _student(db, other_cls, "他班神秘学生")
    db.commit()
    panel = load_class_panel(db, ctx.cls.id)
    html = panel_report_html(panel)
    assert "1班" in html  # 本班数据在
    assert "别班" not in html  # 不含他班信息


# ---------------- 3.1 班级学生列表 ----------------

def test_class_students_list(exercise_client):
    client, db = exercise_client
    ctx = _setup_class_data(db)
    resp = client.get(f"/api/classes/{ctx.cls.id}/students", headers=_auth(ctx.tacc))
    assert resp.status_code == 200
    body = resp.json()
    assert {s["id"] for s in body["items"]} == {ctx.s1.id, ctx.s2.id}
    s1 = next(s for s in body["items"] if s["id"] == ctx.s1.id)
    assert s1["name"] == "张三"
    assert s1["barrier_profile"] == {"concept": 0.6, "reading": 0.2, "expression": 0.2}


def test_class_students_not_found(exercise_client):
    client, db = exercise_client
    ctx = _setup_class_data(db)
    resp = client.get("/api/classes/99999/students", headers=_auth(ctx.tacc))
    assert resp.status_code == 404


# ---------------- 4.1 学生角色 403 ----------------

def test_student_rejected_all_panel_endpoints(exercise_client):
    client, db = exercise_client
    ctx = _setup_class_data(db)
    stu_acc = Account(username="stu", password_hash="x", role=AccountRole.student,
                      role_id=ctx.s1.id)
    db.add(stu_acc)
    db.commit()
    headers = _auth(stu_acc, role="student")
    paths = [
        f"/api/panel/class/{ctx.cls.id}",
        f"/api/panel/class/{ctx.cls.id}/knowledge/氧化还原",
        f"/api/panel/class/{ctx.cls.id}/student/{ctx.s1.id}",
        f"/api/panel/class/{ctx.cls.id}/trend",
        f"/api/panel/export/{ctx.cls.id}",
        f"/api/panel/dashboard/{ctx.teacher.id}",
        f"/api/classes/{ctx.cls.id}/students",
    ]
    for path in paths:
        resp = client.get(path, headers=headers)
        assert resp.status_code == 403, f"{path} 应拒绝 student"


# ---------------- 4.2 教师跨校隔离 ----------------

def test_teacher_cross_school_class_forbidden(exercise_client):
    client, db = exercise_client
    ctx_a = _setup_class_data(db)
    school_b = _school(db, "B校")
    cls_b = _class(db, _grade(db, school_b), "B班")
    t_b, acc_b = _teacher(db, school_b, "赵老师")
    db.commit()
    # 教师 B 访问 A 校班级 → 403
    resp = client.get(f"/api/panel/class/{ctx_a.cls.id}", headers=_auth(acc_b))
    assert resp.status_code == 403
    # 教师 B 访问本校 B 班 → 200
    resp = client.get(f"/api/panel/class/{cls_b.id}", headers=_auth(acc_b))
    assert resp.status_code == 200


def test_admin_cross_school_unrestricted(exercise_client):
    client, db = exercise_client
    ctx_a = _setup_class_data(db)
    school_b = _school(db, "B校")
    cls_b = _class(db, _grade(db, school_b), "B班")
    db.commit()
    admin_acc = Account(username="admin", password_hash="x", role=AccountRole.admin,
                        role_id=1)
    db.add(admin_acc)
    db.commit()
    # admin 跨校访问 → 200
    resp = client.get(f"/api/panel/class/{cls_b.id}", headers=_auth(admin_acc, role="admin"))
    assert resp.status_code == 200
    assert resp.json()["class_overview"]["total_students"] == 0
