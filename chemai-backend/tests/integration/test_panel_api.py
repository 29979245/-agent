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
    Parent,
    ParentNotification,
    Question,
    School,
    Student,
    StudentAnswer,
    StudentParentBinding,
    Teacher,
)
from app.db.models.enums import (
    AccountRole,
    AuditStatus,
    Difficulty,
    ExamStatus,
    ExamType,
    NotificationType,
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


def _parent(db, name, phone):
    p = Parent(name=name, phone=phone)
    db.add(p)
    db.flush()
    return p


def _bind(db, parent, student, status="active"):
    b = StudentParentBinding(parent_id=parent.id, student_id=student.id,
                             bind_code="123456", status=status)
    db.add(b)
    db.flush()
    return b


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
    # 个人成绩趋势：期中 0%（错 1 题）、期末 100%（对 1 题），按时间升序
    assert body["score_trend"] == [
        {"exam_date": ctx.exam1.exam_date.isoformat(), "score": 0.0},
        {"exam_date": ctx.exam2.exam_date.isoformat(), "score": 100.0},
    ]


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


def test_student_detail_kpi_and_activity(exercise_client):
    """KPI 四格（练习次数/平均正确率/最后活跃）+ 最近活动时间线（对/错都算）。"""
    client, db = exercise_client
    ctx = _setup_class_data(db)  # s1：期中错 1、期末对 1 → 作答 2 条
    p = ExamRecord(student_id=ctx.s1.id, class_id=None, name="每日练习",
                   exam_type=ExamType.practice, status=ExamStatus.published,
                   exam_date=_TODAY)
    db.add(p)
    db.flush()
    answered_at = datetime.datetime.utcnow() - datetime.timedelta(days=1)
    _answer(db, ctx.s1, _question(db, "基础"), p, is_correct=True,
            answered_at=answered_at)
    db.commit()

    resp = client.get(f"/api/panel/class/{ctx.cls.id}/student/{ctx.s1.id}",
                      headers=_auth(ctx.tacc))
    assert resp.status_code == 200
    body = resp.json()
    assert body["exercises_completed"] == 1            # 1 个有作答的练习
    assert body["accuracy"] == round(2 / 3, 4)         # 2 对 / 3 作答
    assert body["last_exercise_at"] is not None
    acts = body["recent_activity"]
    assert len(acts) == 3                              # 期末对 / 期中错 / 练习对，倒序
    assert acts[0]["exam_name"] == "期末" and acts[0]["is_correct"] is True
    assert acts[1]["exam_name"] == "期中" and acts[1]["is_correct"] is False
    assert acts[2]["date"] == answered_at.date().isoformat()
    assert all("exam_name" in a and "content" in a and "date" in a for a in acts)


def test_student_detail_empty_kpi(exercise_client):
    """无作答学生：KPI 与活动时间线为空态。"""
    client, db = exercise_client
    school = _school(db)
    cls = _class(db, _grade(db, school), "空班")
    s = _student(db, cls, "无作答")
    teacher, tacc = _teacher(db, school, "陈老师")
    db.commit()

    body = client.get(f"/api/panel/class/{cls.id}/student/{s.id}",
                      headers=_auth(tacc)).json()
    assert body["exercises_completed"] == 0
    assert body["accuracy"] == 0.0
    assert body["last_exercise_at"] is None
    assert body["recent_activity"] == []


# ---------------- 2.3b 发送通知（教师 → 学生绑定家长） ----------------

def test_notify_sends_to_active_bound_parents(exercise_client):
    client, db = exercise_client
    ctx = _setup_class_data(db)
    pa = _parent(db, "张父", "13900000001")
    pb = _parent(db, "张母", "13900000002")
    pc = _parent(db, "旧父", "13900000003")
    _bind(db, pa, ctx.s1)
    _bind(db, pb, ctx.s1)
    _bind(db, pc, ctx.s1, status="inactive")
    db.commit()

    resp = client.post(f"/api/panel/student/{ctx.s1.id}/notify",
                       json={"content": "请督促孩子复习氧化还原"},
                       headers=_auth(ctx.tacc))
    assert resp.status_code == 200
    body = resp.json()
    assert body["sent"] is True and body["notified_parents"] == 2
    notes = (db.query(ParentNotification)
             .filter(ParentNotification.notification_type == NotificationType.reminder)
             .all())
    assert len(notes) == 2
    assert all(n.title == "张三·老师通知" and n.content == "请督促孩子复习氧化还原" for n in notes)
    assert {n.parent_id for n in notes} == {pa.id, pb.id}  # inactive 绑定不通知


def test_notify_no_bound_parent(exercise_client):
    client, db = exercise_client
    ctx = _setup_class_data(db)
    resp = client.post(f"/api/panel/student/{ctx.s2.id}/notify",
                       json={"content": "你好"}, headers=_auth(ctx.tacc))
    assert resp.status_code == 200
    body = resp.json()
    assert body["sent"] is False and body["notified_parents"] == 0
    assert "未绑定" in body["message"]
    assert db.query(ParentNotification).count() == 0


def test_notify_cross_school_forbidden(exercise_client):
    client, db = exercise_client
    ctx = _setup_class_data(db)
    t_b, acc_b = _teacher(db, _school(db, "B校"), "赵老师")
    db.commit()
    resp = client.post(f"/api/panel/student/{ctx.s1.id}/notify",
                       json={"content": "x"}, headers=_auth(acc_b))
    assert resp.status_code == 403


def test_notify_student_rejected(exercise_client):
    client, db = exercise_client
    ctx = _setup_class_data(db)
    stu_acc = Account(username="stu2", password_hash="x", role=AccountRole.student,
                      role_id=ctx.s1.id)
    db.add(stu_acc)
    db.commit()
    resp = client.post(f"/api/panel/student/{ctx.s1.id}/notify",
                       json={"content": "x"}, headers=_auth(stu_acc, role="student"))
    assert resp.status_code == 403


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


# ---------------- 2.5 年级均分趋势 ----------------

def test_grade_trend_has_data(exercise_client):
    client, db = exercise_client
    ctx = _setup_class_data(db)
    resp = client.get(f"/api/panel/grade/{ctx.grade.id}/trend", headers=_auth(ctx.tacc))
    assert resp.status_code == 200
    trend = resp.json()["trend"]
    assert [p["exam_date"] for p in trend] == sorted(p["exam_date"] for p in trend)
    assert trend == [
        {"exam_date": ctx.exam1.exam_date.isoformat(), "avg_score": 50.0},
        {"exam_date": ctx.exam2.exam_date.isoformat(), "avg_score": 100.0},
    ]


def test_grade_trend_aggregates_across_classes(exercise_client):
    """同年级两班同日考试：作答合并后按日期归组算正确率。"""
    client, db = exercise_client
    ctx = _setup_class_data(db)
    cls2 = _class(db, ctx.grade, "2班")
    s3 = _student(db, cls2, "王五", {})
    exam3 = _exam(db, cls2, "期中2", ctx.exam1.exam_date)  # 与 1 班期中同一天
    q3 = _question(db, "化学平衡")
    _answer(db, s3, q3, exam3, is_correct=False)
    db.commit()
    resp = client.get(f"/api/panel/grade/{ctx.grade.id}/trend", headers=_auth(ctx.tacc))
    assert resp.status_code == 200
    trend = resp.json()["trend"]
    # 期中当日合并：1班 1/2 正确 + 2班 0/1 正确 → 1/3
    assert trend[0]["exam_date"] == ctx.exam1.exam_date.isoformat()
    assert trend[0]["avg_score"] == round(1 / 3 * 100, 2)
    assert trend[1]["avg_score"] == 100.0


def test_grade_trend_empty(exercise_client):
    client, db = exercise_client
    school = _school(db)
    grade = _grade(db, school)
    cls = _class(db, grade, "空班")
    teacher, tacc = _teacher(db, school, "陈老师")
    db.commit()
    resp = client.get(f"/api/panel/grade/{grade.id}/trend", headers=_auth(tacc))
    assert resp.status_code == 200
    assert resp.json()["trend"] == []


def test_grade_trend_cross_school_forbidden(exercise_client):
    client, db = exercise_client
    ctx_a = _setup_class_data(db)
    t_b, acc_b = _teacher(db, _school(db, "B校"), "赵老师")
    db.commit()
    resp = client.get(f"/api/panel/grade/{ctx_a.grade.id}/trend", headers=_auth(acc_b))
    assert resp.status_code == 403


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
    # 紧凑趋势：每生最近 6 点；张三 期中 0% / 期末 100%
    assert s1["score_trend"] == [
        {"exam_date": ctx.exam1.exam_date.isoformat(), "score": 0.0},
        {"exam_date": ctx.exam2.exam_date.isoformat(), "score": 100.0},
    ]
    s2 = next(s for s in body["items"] if s["id"] == ctx.s2.id)
    assert s2["score_trend"] == [
        {"exam_date": ctx.exam1.exam_date.isoformat(), "score": 100.0},
        {"exam_date": ctx.exam2.exam_date.isoformat(), "score": 100.0},
    ]


def test_class_students_not_found(exercise_client):
    client, db = exercise_client
    ctx = _setup_class_data(db)
    resp = client.get("/api/classes/99999/students", headers=_auth(ctx.tacc))
    assert resp.status_code == 404


# ---------------- 4.3 /api/classes 教师隔离 ----------------

def test_classes_teacher_sees_only_own_school(exercise_client):
    client, db = exercise_client
    ctx = _setup_class_data(db)  # A 校 1 班
    school_b = _school(db, "B校")
    cls_b = _class(db, _grade(db, school_b), "B班")
    t_b, acc_b = _teacher(db, school_b, "赵老师")
    db.commit()
    resp_a = client.get("/api/classes", headers=_auth(ctx.tacc))
    ids_a = {c["id"] for c in resp_a.json()["items"]}
    assert ctx.cls.id in ids_a
    assert cls_b.id not in ids_a
    resp_b = client.get("/api/classes", headers=_auth(acc_b))
    ids_b = {c["id"] for c in resp_b.json()["items"]}
    assert cls_b.id in ids_b
    assert ctx.cls.id not in ids_b


def test_classes_admin_sees_all(exercise_client):
    client, db = exercise_client
    ctx = _setup_class_data(db)
    cls_b = _class(db, _grade(db, _school(db, "B校")), "B班")
    admin_acc = Account(username="admin", password_hash="x", role=AccountRole.admin, role_id=1)
    db.add(admin_acc)
    db.commit()
    resp = client.get("/api/classes", headers=_auth(admin_acc, role="admin"))
    ids = {c["id"] for c in resp.json()["items"]}
    assert ctx.cls.id in ids
    assert cls_b.id in ids


def test_class_students_activity_state(exercise_client):
    """活跃状态三例：最近作答≤7天活跃、超期作答不活跃、从未作答按注册时间。"""
    client, db = exercise_client
    ctx = _setup_class_data(db)
    # 超期作答学生（10 天前）
    stale = _student(db, ctx.cls, "超期生", {})
    stale_exam = _exam(db, ctx.cls, "早考", _TODAY - datetime.timedelta(days=12))
    _answer(db, stale, _question(db, "基础"), stale_exam, is_correct=True,
            answered_at=datetime.datetime.utcnow() - datetime.timedelta(days=10))
    # 从未作答 + 注册 10 天前 → 不活跃
    never_old = _student(db, ctx.cls, "从未旧", {})
    never_old.created_at = datetime.datetime.utcnow() - datetime.timedelta(days=10)
    # 从未作答 + 注册今天 → 活跃（按注册时间 ≤ 7 天）
    never_fresh = _student(db, ctx.cls, "从未新", {})
    never_fresh.created_at = datetime.datetime.utcnow()
    db.commit()

    resp = client.get(f"/api/classes/{ctx.cls.id}/students", headers=_auth(ctx.tacc))
    assert resp.status_code == 200
    by_name = {s["name"]: s for s in resp.json()["items"]}
    assert by_name["张三"]["is_active"] is True  # 今日作答
    assert by_name["张三"]["last_exercise_at"] is not None
    assert by_name["超期生"]["is_active"] is False  # 10 天前作答
    assert by_name["超期生"]["last_exercise_at"] is not None
    assert by_name["从未旧"]["is_active"] is False  # 从未作答 + 注册超 7 天
    assert by_name["从未旧"]["last_exercise_at"] is None
    assert by_name["从未新"]["is_active"] is True  # 从未作答但注册 ≤ 7 天
    assert by_name["从未新"]["last_exercise_at"] is None


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
