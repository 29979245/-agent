"""临时：OCR 核心流程前后端联通校验（HTTP 级）。

按 ocr.html 消费顺序走一遍：登录 → 班级 → 考试 → 服务状态 → 历史批次 →
上传 → 批次状态 → 自动批改 → 结果(has_options 拆分) → 保存落库 → 终态。
无真实 OCR 引擎，识别完成阶段用直写 DB 模拟（enable_scheduler=False 无竞态）。
结束清理全部临时数据，不影响开发库既有数据。
"""
from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from app.db.models import Question, Student, StudentAnswer, UploadSession
from app.db.models.enums import OCRTaskStatus
from app.db.models.ocr import OCRTask, StudentSubmission
from app.db.session import SessionLocal

BASE = os.environ.get("SMOKE_BASE", "http://localhost:8000")
PASS, FAIL = [], []


def check(name, ok, extra=""):
    (PASS if ok else FAIL).append(name)
    print(("  PASS  " if ok else "  FAIL  ") + name + ("   " + str(extra) if extra else ""))


TEMP_STUDENT_NO = "91001"
TEMP_STUDENT_NAME = "流程测试学生"
temp_student_id = None
batch_id = None
sess = None

try:
    # 1. 登录（教师）
    r = httpx.post(BASE + "/api/auth/login", json={"username": "teacher_demo", "password": "demo123"})
    check("login 教师 teacher_demo/demo123", r.status_code == 200, r.text[:120] if r.status_code != 200 else "")
    token = r.json().get("access_token")
    H = {"Authorization": "Bearer " + token}

    # 2. 班级列表
    r = httpx.get(BASE + "/api/classes", headers=H)
    items = (r.json().get("items") or []) if r.status_code == 200 else []
    cls1 = next((c for c in items if c.get("id") == 1), None)
    check("GET /api/classes 班级列表", r.status_code == 200 and cls1 is not None, json.dumps(cls1, ensure_ascii=False)[:120])

    # 3. 考试列表（前端按 class_name 联动）
    r = httpx.get(BASE + "/api/exam", params={"page_size": 100}, headers=H)
    items = (r.json().get("items") or []) if r.status_code == 200 else []
    ex17 = next((e for e in items if e.get("exam_id") == 17), None)
    check(
        "GET /api/exam 含联动字段 class_name",
        r.status_code == 200 and ex17 is not None and "class_name" in ex17,
        json.dumps(ex17, ensure_ascii=False)[:160],
    )

    # 4. 服务状态
    r = httpx.get(BASE + "/api/ocr/services/status", headers=H)
    d = r.json() if r.status_code == 200 else {}
    check("GET /api/ocr/services/status", r.status_code == 200 and all(k in d for k in ("ocr", "mineru", "vision")), json.dumps(d, ensure_ascii=False)[:120])

    # 5. 历史批次
    r = httpx.get(BASE + "/api/ocr/tasks", headers=H)
    d = r.json() if r.status_code == 200 else {}
    check("GET /api/ocr/tasks 历史批次", r.status_code == 200 and "batches" in d, json.dumps(d, ensure_ascii=False)[:120])

    # ---- 建临时学生 + 上传 + 模拟识别 ----
    db = SessionLocal()
    try:
        qs = db.query(Question).filter(Question.record_id == 17).order_by(Question.id).all()
        bank = {i + 1: q.answer for i, q in enumerate(qs)}
        has_opt = {i + 1: bool(q.options) for i, q in enumerate(qs)}
        print("  [db] exam17 bank:", json.dumps(bank, ensure_ascii=False))
        print("  [db] exam17 has_options:", has_opt)

        stu = Student(class_id=1, student_no=TEMP_STUDENT_NO, name=TEMP_STUDENT_NAME)
        db.add(stu)
        db.flush()
        temp_student_id = stu.id
        # 立即提交释放 SQLite 写锁，否则下面上传端点（服务端写库）会被本会话的写事务阻塞而挂起
        db.commit()
        print("  [db] temp student id:", temp_student_id)

        # 6. 上传（两张假 PNG，合法扩展名/大小）
        png = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A]) + b"\x00" * 64
        files = [
            ("files", ("card1.png", io.BytesIO(png), "image/png")),
            ("files", ("card2.png", io.BytesIO(png), "image/png")),
        ]
        r = httpx.post(BASE + "/api/ocr/tasks/batch", files=files, headers=H)
        check("POST /api/ocr/tasks/batch 上传建批次", r.status_code == 200, r.text[:160] if r.status_code != 200 else "")
        up = r.json()
        batch_id = up.get("batch_id")
        task_ids = up.get("task_ids") or []
        check("  上传返回 batch_id+task_ids", batch_id and len(task_ids) == 2, json.dumps(up, ensure_ascii=False)[:160])

        # 7. 批次状态（初始 uploaded）
        r = httpx.get(BASE + f"/api/ocr/tasks/batch/{batch_id}", headers=H)
        agg = r.json()
        check("GET 批次状态(initial)", r.status_code == 200 and agg.get("status") in ("uploaded", "pending") and agg.get("total") == 2, json.dumps(agg, ensure_ascii=False)[:160])

        # 模拟识别完成：直写 DB 置 done + 带答案（enable_scheduler=False 无调度覆盖）
        for t in db.query(OCRTask).filter(OCRTask.session_id == batch_id).order_by(OCRTask.id).all():
            t.status = OCRTaskStatus.done
            t.progress = 100
            t.result = {
                "student_no": TEMP_STUDENT_NO,
                "student_name": TEMP_STUDENT_NAME,
                "answers": [
                    {"question_no": 1, "answer": bank[1]},
                    {"question_no": 2, "answer": bank[2]},
                    {"question_no": 3, "answer": "WRONG_ANSWER_XYZ"},
                ],
            }
        db.commit()

        r = httpx.get(BASE + f"/api/ocr/tasks/batch/{batch_id}", headers=H)
        agg = r.json()
        check("  模拟识别后 done=2", agg.get("done") == 2, json.dumps(agg, ensure_ascii=False)[:160])

        # 8. 自动批改
        r = httpx.post(BASE + "/api/grading/run", json={"batch_id": batch_id, "exam_id": 17}, headers=H)
        g = r.json() if r.status_code == 200 else {}
        tasks = g.get("tasks") or []
        it = tasks[0].get("items", []) if tasks else []
        ok = r.status_code == 200 and g.get("graded") == 2 and g.get("source") == "bank"
        check("POST /api/grading/run 自动批改", ok and all("has_options" in i for i in it), json.dumps(g, ensure_ascii=False)[:300])

        # 9. 结果端点 + has_options 拆分
        r = httpx.get(BASE + f"/api/grading/results/{batch_id}", headers=H)
        res = r.json() if r.status_code == 200 else {}
        g0 = ((res.get("tasks") or [{}])[0].get("grading")) or {}
        items = g0.get("items") or []
        q1 = next((i for i in items if i.get("question_no") == 1), {})
        q2 = next((i for i in items if i.get("question_no") == 2), {})
        q3 = next((i for i in items if i.get("question_no") == 3), {})
        ok_split = q1.get("has_options") is False and q2.get("has_options") is True and q3.get("has_options") is False
        detail = [{"qno": i.get("question_no"), "ho": i.get("has_options"), "ok": i.get("is_correct"), "sa": i.get("student_answer")} for i in items]
        check("GET /api/grading/results has_options 拆分", r.status_code == 200 and ok_split, json.dumps(detail, ensure_ascii=False))

        # 10. 保存落库
        r = httpx.post(BASE + "/api/grading/save", json={"batch_id": batch_id, "exam_id": 17}, headers=H)
        sv = r.json() if r.status_code == 200 else {}
        check("POST /api/grading/save 分档落库", r.status_code == 200 and sv.get("saved", 0) >= 1 and "diagnosis" in sv, json.dumps(sv, ensure_ascii=False)[:200])

        # 11. 保存后终态
        r = httpx.get(BASE + f"/api/grading/results/{batch_id}", headers=H)
        res2 = r.json() if r.status_code == 200 else {}
        check("保存后批次终态 done", res2.get("status") == "done", json.dumps(res2, ensure_ascii=False)[:160])

        # 12. DB 落库核对
        rows = db.query(StudentAnswer).filter(StudentAnswer.student_id == temp_student_id, StudentAnswer.exam_id == 17).count()
        subs = db.query(StudentSubmission).filter(StudentSubmission.session_id == batch_id).count()
        check("DB StudentAnswer 模式1 展开", rows >= 1, f"rows={rows}")
        check("DB StudentSubmission 未误写", subs == 0, f"subs={subs}")

    finally:
        # 清理：临时学生 + OCR 行 + 落库行 + 上传文件
        try:
            file_paths = []
            if batch_id:
                file_paths = [t.file_path for t in db.query(OCRTask).filter(OCRTask.session_id == batch_id).all()]
            db.query(StudentAnswer).filter(StudentAnswer.exam_id == 17, StudentAnswer.student_id == temp_student_id).delete(synchronize_session=False)
            if batch_id:
                db.query(OCRTask).filter(OCRTask.session_id == batch_id).delete(synchronize_session=False)
                db.query(StudentSubmission).filter(StudentSubmission.session_id == batch_id).delete(synchronize_session=False)
                db.query(UploadSession).filter(UploadSession.id == batch_id).delete(synchronize_session=False)
            if temp_student_id:
                db.query(Student).filter(Student.id == temp_student_id).delete(synchronize_session=False)
            db.commit()
            for p in file_paths:
                if p:
                    Path(p).unlink(missing_ok=True)
            print("  [cleanup] ok")
        except Exception as e:
            db.rollback()
            print("  [cleanup] failed:", e)
        db.close()

except Exception as e:
    print("  [harness] unexpected error:", e)
    import traceback
    traceback.print_exc()

print(f"=== {len(PASS)} PASS / {len(FAIL)} FAIL ===")
sys.exit(1 if FAIL else 0)
