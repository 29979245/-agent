"""预警 API L2 集成测试（early-warning-engine，task 4.1-4.5/5.2）。

覆盖：pending 列表/空/class 筛选、学生历史（含已处理/倒序）、process 两种 action 与 404、
手动触发 check、班级汇总、student 403、教师跨校隔离。
"""
import datetime

from app.core.security import create_token
from app.db.models import (
    Account,
    Class,
    Grade,
    School,
    Student,
    Teacher,
    WarningLog,
)
from app.db.models.enums import (
    AccountRole,
    WarningLevel,
    WarningStatus,
    WarningType,
)
from tests.integration.conftest import exercise_client  # noqa: F401

_TODAY = datetime.date.today()


def _school(db, name="A校"):
    s = School(name=name, current_semester="2026-1")
    db.add(s)
    db.flush()
    return s


def _grade(db, school):
    g = Grade(school_id=school.id, name="高一", academic_year="2026")
    db.add(g)
    db.flush()
    return g


def _class(db, grade, name="1班"):
    c = Class(grade_id=grade.id, name=name)
    db.add(c)
    db.flush()
    return c


def _student(db, cls, name):
    s = Student(
        class_id=cls.id, name=name, barrier_profile={},
        created_at=datetime.datetime.utcnow() - datetime.timedelta(days=10),
    )
    db.add(s)
    db.flush()
    return s


def _teacher(db, school, name):
    t = Teacher(school_id=school.id, name=name, phone=f"13{name.encode().hex()[:8]}")
    db.add(t)
    db.flush()
    acc = Account(username=f"t{name}", password_hash="x", role=AccountRole.teacher, role_id=t.id)
    db.add(acc)
    db.flush()
    return t, acc


def _auth(account, role="teacher"):
    token = create_token(user_id=account.id, role=role, school_id=1)
    return {"Authorization": f"Bearer {token}"}


def _warning(db, student, wtype, level, **kw):
    w = WarningLog(
        student_id=student.id, warning_type=wtype, level=level,
        title="预警", content="摘要", data={"k": 1}, **kw,
    )
    db.add(w)
    db.flush()
    return w


def _setup(db, school_name="A校"):
    school = _school(db, school_name)
    grade = _grade(db, school)
    cls = _class(db, grade)
    s1 = _student(db, cls, "张三")
    s2 = _student(db, cls, "李四")
    teacher, tacc = _teacher(db, school, "王老师")
    db.commit()
    return school, grade, cls, s1, s2, teacher, tacc


# ---------------- 4.1 GET /pending ----------------

def test_pending_warnings_list_sorted(exercise_client):
    client, db = exercise_client
    _, _, cls, s1, s2, _, tacc = _setup(db)
    _warning(db, s1, WarningType.no_login, WarningLevel.warning,
             created_at=datetime.datetime(2026, 8, 20, 10, 0, 0))
    _warning(db, s2, WarningType.score_drop, WarningLevel.critical,
             created_at=datetime.datetime(2026, 8, 25, 10, 0, 0))
    db.commit()
    r = client.get("/api/warning/pending", headers=_auth(tacc))
    assert r.status_code == 200
    body = r.json()
    assert len(body["items"]) == 2
    # 触发时间倒序：score_drop（8-25）在前
    assert body["items"][0]["warning_type"] == "score_drop"
    assert body["items"][0]["level"] == "critical"
    assert body["items"][0]["student_name"] == "李四"
    assert body["items"][0]["class_name"] == "1班"
    assert {i["status"] for i in body["items"]} == {"pending"}


def test_pending_warnings_empty(exercise_client):
    client, db = exercise_client
    _, _, _, _, _, _, tacc = _setup(db)
    db.commit()
    r = client.get("/api/warning/pending", headers=_auth(tacc))
    assert r.status_code == 200
    assert r.json()["items"] == []


def test_pending_warnings_filter_by_class(exercise_client):
    client, db = exercise_client
    school, grade, cls, s1, _, _, tacc = _setup(db)
    cls2 = _class(db, grade, "2班")
    s3 = _student(db, cls2, "王五")
    _warning(db, s1, WarningType.no_login, WarningLevel.warning)
    _warning(db, s3, WarningType.score_drop, WarningLevel.critical)
    db.commit()
    r = client.get(f"/api/warning/pending?class_id={cls.id}", headers=_auth(tacc))
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["student_name"] == "张三"


# ---------------- 4.2 GET /student/{student_id} ----------------

def test_student_history_includes_processed(exercise_client):
    client, db = exercise_client
    _, _, _, s1, _, _, tacc = _setup(db)
    _warning(db, s1, WarningType.no_login, WarningLevel.warning,
             status=WarningStatus.pending, created_at=datetime.datetime(2026, 8, 20, 10, 0, 0))
    _warning(db, s1, WarningType.score_drop, WarningLevel.critical,
             status=WarningStatus.processed, created_at=datetime.datetime(2026, 8, 25, 10, 0, 0))
    db.commit()
    r = client.get(f"/api/warning/student/{s1.id}", headers=_auth(tacc))
    assert r.status_code == 200
    body = r.json()
    assert len(body["items"]) == 2
    assert body["items"][0]["status"] == "processed"  # 倒序：更新在前
    assert {i["status"] for i in body["items"]} == {"pending", "processed"}


# ---------------- 4.3 PUT /{warning_id}/process ----------------

def test_process_warning_processed(exercise_client):
    client, db = exercise_client
    _, _, _, s1, _, _, tacc = _setup(db)
    w = _warning(db, s1, WarningType.no_login, WarningLevel.warning)
    db.commit()
    r = client.put(f"/api/warning/{w.id}/process", headers=_auth(tacc),
                   json={"action": "processed", "note": "已电话联系家长"})
    assert r.status_code == 200
    assert r.json()["status"] == "processed"
    db.refresh(w)
    assert w.status == WarningStatus.processed
    assert w.processed_note == "已电话联系家长"
    assert w.processed_at is not None
    assert w.processed_by is not None


def test_process_warning_ignored(exercise_client):
    client, db = exercise_client
    _, _, _, s1, _, _, tacc = _setup(db)
    w = _warning(db, s1, WarningType.no_login, WarningLevel.warning)
    db.commit()
    r = client.put(f"/api/warning/{w.id}/process", headers=_auth(tacc),
                   json={"action": "ignored"})
    assert r.status_code == 200
    assert r.json()["status"] == "ignored"
    db.refresh(w)
    assert w.status == WarningStatus.ignored


def test_process_warning_not_found(exercise_client):
    client, db = exercise_client
    _, _, _, _, _, _, tacc = _setup(db)
    db.commit()
    r = client.put("/api/warning/9999/process", headers=_auth(tacc),
                   json={"action": "processed"})
    assert r.status_code == 404


# ---------------- 4.4 POST /check ----------------

def test_trigger_check_creates_warnings(exercise_client):
    client, db = exercise_client
    # 学生从未作答且 created_at 10 天前 → 全部命中 no_login
    _, _, _, s1, s2, _, tacc = _setup(db)
    db.commit()
    r = client.post("/api/warning/check", headers=_auth(tacc))
    assert r.status_code == 200
    body = r.json()
    assert body["created"] == 2
    assert body["by_type"].get("no_login") == 2
    assert body["failed"] == 0
    assert db.query(WarningLog).count() == 2


# ---------------- 4.5 GET /class/{class_id}/summary ----------------

def test_class_summary(exercise_client):
    client, db = exercise_client
    _, _, cls, s1, s2, _, tacc = _setup(db)
    _warning(db, s1, WarningType.no_login, WarningLevel.warning)
    _warning(db, s2, WarningType.score_drop, WarningLevel.critical)
    _warning(db, s2, WarningType.high_error_rate, WarningLevel.info)
    db.commit()
    r = client.get(f"/api/warning/class/{cls.id}/summary", headers=_auth(tacc))
    assert r.status_code == 200
    body = r.json()
    assert body["class_name"] == "1班"
    assert body["total"] == 3
    assert body["by_type"]["no_login"] == 1
    assert body["by_type"]["score_drop"] == 1
    assert body["by_type"]["high_error_rate"] == 1
    assert body["by_level"]["warning"] == 1
    assert body["by_level"]["critical"] == 1
    assert body["by_level"]["info"] == 1
    assert body["critical_count"] == 1


# ---------------- 5.2 权限与隔离 ----------------

def test_student_access_any_warning_endpoint_forbidden(exercise_client):
    client, db = exercise_client
    _, _, _, s1, _, _, _ = _setup(db)
    _warning(db, s1, WarningType.no_login, WarningLevel.warning)
    db.commit()
    s_acc = Account(username="stu1", password_hash="x", role=AccountRole.student, role_id=s1.id)
    db.add(s_acc)
    db.commit()
    for url in ("/api/warning/pending", f"/api/warning/student/{s1.id}",
                f"/api/warning/class/{s1.class_id}/summary"):
        r = client.get(url, headers=_auth(s_acc, role="student"))
        assert r.status_code == 403, url


def test_teacher_cross_school_class_forbidden(exercise_client):
    client, db = exercise_client
    school_a, _, cls_a, s1, _, _, _ = _setup(db, "A校")
    _warning(db, s1, WarningType.no_login, WarningLevel.warning)
    school_b = _school(db, "B校")
    _, tacc_b = _teacher(db, school_b, "赵老师")
    db.commit()
    # B 校教师访问 A 校班级/学生预警 → 403
    r = client.get(f"/api/warning/pending?class_id={cls_a.id}", headers=_auth(tacc_b))
    assert r.status_code == 403
    r = client.get(f"/api/warning/student/{s1.id}", headers=_auth(tacc_b))
    assert r.status_code == 403
    r = client.get(f"/api/warning/class/{cls_a.id}/summary", headers=_auth(tacc_b))
    assert r.status_code == 403
    w = db.query(WarningLog).one()
    r = client.put(f"/api/warning/{w.id}/process", headers=_auth(tacc_b),
                   json={"action": "processed"})
    assert r.status_code == 403


def test_teacher_own_school_allowed(exercise_client):
    client, db = exercise_client
    _, _, cls_a, s1, _, tacc_a, _ = _setup(db, "A校")
    w = _warning(db, s1, WarningType.no_login, WarningLevel.warning)
    db.commit()
    r = client.get(f"/api/warning/pending?class_id={cls_a.id}", headers=_auth(tacc_a))
    assert r.status_code == 200
    r = client.get(f"/api/warning/student/{s1.id}", headers=_auth(tacc_a))
    assert r.status_code == 200
    r = client.get(f"/api/warning/class/{cls_a.id}/summary", headers=_auth(tacc_a))
    assert r.status_code == 200
    r = client.put(f"/api/warning/{w.id}/process", headers=_auth(tacc_a),
                   json={"action": "processed", "note": "本校处理"})
    assert r.status_code == 200
