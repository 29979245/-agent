"""家长端前端契约冒烟：对运行中的 8000 端口做家长端全链路请求。

种子学生/家长/绑定/作答/通知 → 家长登录 → children → child report → notifications
→ 标记已读 → 绑定新子女（正/负）。验证前端依赖的响应契约与错误码。
用法：python scripts/smoke_parent_frontend.py（需先启动 uvicorn）
"""
import datetime
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

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
)
from app.db.models.enums import (
    AccountRole,
    Difficulty,
    ExamStatus,
    ExamType,
    NotificationType,
    ParentBindingRelation,
    ParentBindingStatus,
    QuestionSource,
)
from app.db.session import SessionLocal

BASE = "http://localhost:8000"
PARENT_PHONE = "13900008888"
BIND_CODE = "A1B2C3"


def req(method, path, token=None, payload=None):
    url = BASE + path
    data = json.dumps(payload).encode() if payload is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(r) as resp:
            body = resp.read()
            if resp.headers.get("content-type", "").startswith("application/json"):
                return resp.status, json.loads(body)
            return resp.status, body
    except urllib.error.HTTPError as e:
        body = e.read()
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, body.decode("utf-8", "replace")


def seed():
    db = SessionLocal()
    try:
        # 幂等清理（先子后父，避免 FK 约束）
        parents = db.query(Parent).filter(Parent.phone == PARENT_PHONE).all()
        for p in parents:
            db.query(StudentParentBinding).filter(StudentParentBinding.parent_id == p.id).delete(synchronize_session=False)
            db.query(ParentNotification).filter(ParentNotification.parent_id == p.id).delete(synchronize_session=False)
        db.query(Parent).filter(Parent.phone == PARENT_PHONE).delete(synchronize_session=False)
        db.query(ExamRecord).filter(ExamRecord.name.like("冒烟家长%")).delete(synchronize_session=False)
        db.query(Student).filter(Student.name.like("冒烟学生%")).delete(synchronize_session=False)
        db.query(Question).filter(Question.content.like("冒烟家长题%")).delete(synchronize_session=False)
        db.query(Account).filter(Account.username.like("smoke_parent%")).delete(synchronize_session=False)
        db.query(Class).filter(Class.name == "冒烟家长班").delete(synchronize_session=False)
        db.query(Grade).filter(Grade.name == "冒烟年级").delete(synchronize_session=False)
        db.query(School).filter(School.name == "冒烟学校2").delete(synchronize_session=False)
        db.commit()

        school = School(name="冒烟学校2", current_semester="2026-1")
        db.add(school)
        db.flush()
        grade = Grade(school_id=school.id, name="冒烟年级", academic_year="2026")
        db.add(grade)
        db.flush()
        cls = Class(grade_id=grade.id, name="冒烟家长班")
        db.add(cls)
        db.flush()

        # 两名学生：s1 已有绑定（登录用），bind_code 存 BIND_CODE 供「重复绑定」路径先过码检；
        # s2 供绑定新子女正/负路径
        s1 = Student(class_id=cls.id, name="冒烟学生甲", student_no="1001", bind_code=BIND_CODE)
        s2 = Student(class_id=cls.id, name="冒烟学生乙", student_no="1002", bind_code="X9Y8Z7")
        db.add_all([s1, s2])
        db.flush()

        q1 = Question(content="冒烟家长题1：$\\ce{2H2 + O2 -> 2H2O}$", answer="a",
                      knowledge_points="氧化还原反应,化学方程式配平",
                      source=QuestionSource.manual, difficulty=Difficulty.easy)
        q2 = Question(content="冒烟家长题2：$\\ce{NaOH + HCl -> NaCl + H2O}$", answer="b",
                      knowledge_points="离子反应,化学方程式配平",
                      source=QuestionSource.manual, difficulty=Difficulty.easy)
        db.add_all([q1, q2])
        db.flush()

        # 按 ADR-0002：练习记录归属学生（student_id），_practice_records 以 student_id 过滤
        exam = ExamRecord(class_id=cls.id, student_id=s1.id, name="冒烟家长练习",
                          exam_type=ExamType.practice, status=ExamStatus.completed,
                          exam_date=datetime.date.today())
        db.add(exam)
        db.flush()

        now = datetime.datetime.utcnow()
        # 4 条作答：2 对 2 错，均在本周
        answers = [
            StudentAnswer(student_id=s1.id, question_id=q1.id, exam_id=exam.id, is_correct=True, answered_at=now, consecutive_correct=1),
            StudentAnswer(student_id=s1.id, question_id=q2.id, exam_id=exam.id, is_correct=True, answered_at=now, consecutive_correct=1),
            StudentAnswer(student_id=s1.id, question_id=q1.id, exam_id=exam.id, is_correct=False, answered_at=now, consecutive_errors=1),
            StudentAnswer(student_id=s1.id, question_id=q2.id, exam_id=exam.id, is_correct=False, answered_at=now, consecutive_errors=1),
        ]
        db.add_all(answers)

        parent = Parent(name="冒烟家长", phone=PARENT_PHONE)
        db.add(parent)
        db.flush()
        db.add(StudentParentBinding(parent_id=parent.id, student_id=s1.id, bind_code=BIND_CODE,
                                    relation=ParentBindingRelation.guardian, status=ParentBindingStatus.active))
        db.add(ParentNotification(parent_id=parent.id, notification_type=NotificationType.daily_report,
                                  title="今日练习报告", content="孩子今日完成练习 4 题，正确率 50%。", is_read=False))
        db.add(ParentNotification(parent_id=parent.id, notification_type=NotificationType.score_alert,
                                  title="成绩预警", content="孩子最近成绩出现下滑，建议关注。", is_read=True))
        db.commit()
        return {"sid1": s1.id, "sid2": s2.id}
    finally:
        db.close()


def main():
    ids = seed()
    fails = []

    def check(name, ok, extra=""):
        print(("PASS" if ok else "FAIL"), name, extra)
        if not ok:
            fails.append(name)

    # 1) 家长登录
    st, login = req("POST", "/api/parent/login", payload={"phone": PARENT_PHONE, "bind_code": BIND_CODE})
    check("login", st == 200 and login.get("access_token") and login.get("role") == "parent", f"status={st} role={login.get('role')}")
    token = login.get("access_token", "")
    check("login fields", {"access_token", "refresh_token", "user_id", "role", "name", "school_id", "role_id"} <= set(login), f"keys={sorted(login.keys())}")

    # 2) 子女列表
    st, children = req("GET", "/api/parent/children", token=token)
    check("children", st == 200 and any(c["student_id"] == ids["sid1"] for c in children.get("children", [])), f"status={st}")

    # 3) 子女报告（含前端渲染字段）
    st, report = req("GET", f"/api/parent/child/{ids['sid1']}/report", token=token)
    stats = report.get("stats", {})
    check("report", st == 200 and stats.get("weekly_exercises", 0) >= 1, f"status={st}")
    check("report fields", {"weekly_exercises", "weekly_accuracy", "streak_days", "total_practices", "total_answers", "overall_accuracy"} <= set(stats),
          f"stats keys={sorted(stats.keys())}")
    check("report kp", isinstance(report.get("knowledge_points"), list) and len(report.get("knowledge_points", [])) >= 1,
          f"kps={len(report.get('knowledge_points', []))}")
    check("report style/timeline", isinstance(report.get("learning_style"), str) and len(report.get("learning_style", "")) > 0
          and isinstance(report.get("timeline"), list), f"keys={sorted(report.keys())}")

    # 4) 通知列表（分页倒序，含已读状态）
    st, notif = req("GET", "/api/parent/notifications?limit=20&offset=0", token=token)
    check("notifications", st == 200 and notif.get("total", 0) >= 2, f"status={st} total={notif.get('total')}")
    first = (notif.get("notifications") or [{}])[0]
    check("notif fields", {"id", "notification_type", "title", "content", "is_read", "created_at"} <= set(first),
          f"keys={sorted(first.keys())}")

    # 5) 标记已读
    nid = first.get("id")
    if nid and not first.get("is_read"):
        st, marked = req("PUT", f"/api/parent/notifications/{nid}/read", token=token)
        check("mark read", st == 200 and marked.get("is_read") is True, f"status={st}")
        st2, notif2 = req("GET", "/api/parent/notifications?limit=20&offset=0", token=token)
        target = [n for n in notif2.get("notifications", []) if n["id"] == nid]
        check("mark read persisted", bool(target) and target[0]["is_read"] is True, f"status={st2}")

    # 6) 绑定码不匹配（错误码契约，前端映射 BIND_CODE_MISMATCH；s2 码未消费，真·不匹配）
    st, bad = req("POST", "/api/parent/bind", token=token,
                  payload={"student_id": ids["sid2"], "bind_code": "000000", "relation": "guardian"})
    check("bind mismatch", bad.get("error_code") == "BIND_CODE_MISMATCH", f"status={st} error_code={bad.get('error_code')}")

    # 7) 绑定新子女正路径（s2 绑定码 X9Y8Z7）
    st, bind = req("POST", "/api/parent/bind", token=token,
                   payload={"student_id": ids["sid2"], "bind_code": "X9Y8Z7", "relation": "guardian"})
    check("bind ok", st == 200 and bind.get("binding_id"), f"status={st} body={bind}")

    # 8) 重复绑定已存在（码检通过后命中 BINDING_EXISTS）
    st, dup = req("POST", "/api/parent/bind", token=token,
                  payload={"student_id": ids["sid1"], "bind_code": BIND_CODE, "relation": "guardian"})
    check("bind exists", dup.get("error_code") == "BINDING_EXISTS", f"status={st} error_code={dup.get('error_code')}")

    print("\nRESULT:", "ALL PASS" if not fails else f"FAILED: {fails}")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
