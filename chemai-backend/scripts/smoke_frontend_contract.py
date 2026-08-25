"""前端契约冒烟测试：对运行中的 8000 端口做教师端全链路请求。

种子教师 → 登录 → 题库 CRUD/导入 → 考试全生命周期 → 导出。
用法：python scripts/smoke_frontend_contract.py（需先启动 uvicorn）
"""
import io
import json
import sys
import time
import urllib.request
import urllib.error
import urllib.parse

from app.core.security import hash_password
from app.db.models import (
    Account,
    Class,
    ExamRecord,
    Grade,
    Question,
    School,
    Teacher,
)
from app.db.models.enums import AccountRole, Difficulty, TeacherStatus
from app.db.session import SessionLocal

BASE = "http://localhost:8000"


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


def main():
    db = SessionLocal()
    # 清理可能存在的同名种子，保证幂等（考试引用班级 FK，须先删考试再删班级）
    smoke_class_ids = [c.id for c in db.query(Class).filter(Class.name == "冒烟班").all()]
    for cid in smoke_class_ids:
        db.query(ExamRecord).filter(ExamRecord.class_id == cid).delete(synchronize_session=False)
    db.query(Account).filter(Account.username.like("smoke_%")).delete(synchronize_session=False)
    db.query(Teacher).filter(Teacher.name == "冒烟教师").delete(synchronize_session=False)
    db.query(Class).filter(Class.name == "冒烟班").delete(synchronize_session=False)
    db.commit()

    school = School(name="冒烟学校", current_semester="2026-1")
    db.add(school)
    db.flush()
    grade = Grade(school_id=school.id, name="高一", academic_year="2026")
    db.add(grade)
    db.flush()
    cls = Class(grade_id=grade.id, name="冒烟班")
    db.add(cls)
    db.flush()
    class_id = cls.id
    teacher = Teacher(school_id=school.id, name="冒烟教师", phone="13800009999", status=TeacherStatus.approved)
    db.add(teacher)
    db.flush()
    db.add(Account(username="smoke_t", password_hash=hash_password("Smoke!2026"), role=AccountRole.teacher, role_id=teacher.id))
    db.commit()
    db.close()

    fails = []

    def check(name, ok, extra=""):
        print(("PASS" if ok else "FAIL"), name, extra)
        if not ok:
            fails.append(name)

    # 登录
    st, login = req("POST", "/api/auth/login", payload={"username": "smoke_t", "password": "Smoke!2026"})
    check("login", st == 200 and login.get("access_token"), f"status={st}")
    token = login.get("access_token", "")
    auth = {"token": token}

    # 班级
    st, classes = req("GET", "/api/classes", token=token)
    check("classes", st == 200 and any(c["id"] == class_id for c in classes["items"]), f"status={st}")

    # 题库：建夹 → 列表（含预设+我的并集） → 详情
    st, created = req("POST", "/api/exam-bank/exam-sets", token=token, payload={"name": "冒烟题库"})
    check("create set", st == 200 and created.get("id"), f"status={st}")
    set_id = created.get("id")

    st, sets = _get_with_query("/api/exam-bank/exam-sets", {"teacher_id": 1, "page": 1, "page_size": 20}, token)
    check("list sets", st == 200 and isinstance(sets.get("items"), list), f"status={st}")

    st, detail = req("GET", f"/api/exam-bank/exam-sets/{set_id}", token=token)
    check("set detail", st == 200 and "questions" in detail, f"status={st}")

    # 生成一道题（真实生成管线可能依赖 LLM，跳过：直接手动插入 Question 表模拟已入库题目）
    q = Question(content="冒烟：$\\\\ce{2H2 + O2 -> 2H2O}$", answer="2H2+O2=2H2O",
                 knowledge_points="氧化还原", source="manual", difficulty=Difficulty.easy)
    db = SessionLocal()
    db.add(q)
    db.flush()
    qid = q.id
    db.commit()
    db.close()

    # 批量导入到文件夹
    st, imp = req("POST", f"/api/exam-bank/exam-sets/{set_id}/import-questions",
                  token=token, payload={"question_ids": [qid]})
    check("import questions", st == 200 and imp.get("added") == 1, f"status={st} extra={imp}")

    st, detail2 = req("GET", f"/api/exam-bank/exam-sets/{set_id}", token=token)
    qs = detail2.get("questions", [])
    check("set detail has question", st == 200 and len(qs) == 1 and "question_id" in qs[0] and "audit_status" in qs[0], f"status={st}")

    # 历史真题搜索
    st, hist = _get_with_query("/api/exam-bank/historical", {"keyword": "离子"}, token)
    check("historical search", st == 200 and isinstance(hist.get("items"), list), f"status={st}")

    # 考试：创建 → 加题 → 发布 → 阅卷 → 完成 → 归档
    st, exam = req("POST", "/api/exam/create", token=token,
                   payload={"class_id": class_id, "name": "冒烟期中", "exam_type": "exam"})
    check("create exam", st == 200 and exam.get("exam_id"), f"status={st}")
    exam_id = exam.get("exam_id")

    st, addq = req("POST", f"/api/exam/{exam_id}/questions", token=token, payload={"question_ids": [qid]})
    check("add exam questions", st == 200 and addq.get("added") == 1, f"status={st}")

    st, questions = req("GET", f"/api/exam/{exam_id}/questions", token=token)
    check("list exam questions", st == 200 and len(questions.get("items", [])) == 1, f"status={st}")

    st, pub = req("POST", f"/api/exam/{exam_id}/publish", token=token)
    check("publish", st == 200 and pub.get("status") == "published", f"status={st}")

    st, grading = req("POST", f"/api/exam/{exam_id}/start-grading", token=token)
    check("start grading", st == 200 and grading.get("status") == "grading", f"status={st}")

    st, fin = req("POST", f"/api/exam/{exam_id}/finalize", token=token)
    check("finalize", st == 200 and fin.get("status") == "completed", f"status={st}")

    st, arch = req("POST", f"/api/exam/{exam_id}/archive", token=token)
    check("archive", st == 200 and arch.get("status") == "archived", f"status={st}")

    # 考试列表（六态字段）
    st, exams = _get_with_query("/api/exam", {"page": 1, "page_size": 20}, token)
    check("list exams", st == 200, f"status={st}")
    top = exams.get("items", [])[0]
    check("exam list fields", "exam_id" in top and "class_name" in top and "status" in top and "exam_date" in top and "question_count" in top, f"keys={sorted(top.keys())}")

    # 导出 docx（blob）
    st, blob = req("GET", f"/api/question/export/{exam_id}?with_answers=true", token=token)
    check("export docx", st == 200 and isinstance(blob, bytes) and blob[:2] == b"PK", f"status={st} bytes={len(blob) if isinstance(blob, bytes) else ''}")

    print("\nRESULT:", "ALL PASS" if not fails else f"FAILED: {fails}")
    return 0 if not fails else 1


def _get_with_query(path, query, token):
    qs = urllib.parse.urlencode(query)
    return req("GET", f"{path}?{qs}", token=token)


if __name__ == "__main__":
    sys.exit(main())
