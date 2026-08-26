"""端到端闭环集成测试（task 9.3）：出题 → 提交 → 诊断降级 → 复习同步 → 复习提交 → 错题 → 变式 → 训练 → 每日调度。

一条测试走通全链路，验证各服务/API 在真实调用链上彼此衔接。
"""
import datetime

from app.db.models import ExamRecord, ExamType, ReviewTask, StudentAnswer
from app.db.models.enums import ReviewTaskStatus
from app.services.exercise.daily import DailyPracticeScheduler
from app.services.question.historical import reload_bank
from tests.integration.conftest import _account, _headers, _student

NOW = datetime.datetime(2026, 8, 26, 8, 0, 0)


def _load_bank(tmp_path):
    p = tmp_path / "全国卷" / "2024"
    p.mkdir(parents=True, exist_ok=True)
    (p / "真题.json").write_text('{"questions": ['
        '{"id": "q1", "content": "真题A", "answer": "B", "knowledge_points": ["氧化还原反应"], "difficulty": "easy"},'
        '{"id": "q2", "content": "真题B", "answer": "C", "knowledge_points": ["化学平衡"], "difficulty": "easy"},'
        '{"id": "q3", "content": "真题C", "answer": "D", "knowledge_points": ["离子反应"], "difficulty": "easy"},'
        '{"id": "q4", "content": "真题D", "answer": "A", "knowledge_points": ["氧化还原反应"], "difficulty": "easy"}'
        "]}", encoding="utf-8")
    return reload_bank(tmp_path)


def test_end_to_end_closed_loop(exercise_client, tmp_path):
    _load_bank(tmp_path)
    client, db = exercise_client
    stu = _student(db)
    acc = _account(db, stu)
    headers = _headers(acc)

    # 1. 出题：每日调度生成 per-student 每日练习（复制真题入库）
    result = DailyPracticeScheduler(db).create_daily_practice(stu, now=NOW)
    exam = db.get(ExamRecord, result["exam_id"])
    qids = [q.id for q in exam.questions]
    assert exam.student_id == stu.id and exam.exam_type == ExamType.practice
    assert len(qids) >= 2

    # 2. 提交：全部答错 → 0 分，逐题落 StudentAnswer
    resp = client.post("/api/practice/submit", headers=headers, json={
        "practice_id": exam.id,
        "answers": [{"question_id": qid, "selected_option": "x"} for qid in qids],
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["score"] == 0 and body["total"] == len(qids)
    assert all(not r["is_correct"] for r in body["results"])

    # 3. 自动诊断降级：LLM 不可用 → 规则单路融合（source=rule），不阻塞响应
    answers = db.query(StudentAnswer).filter(StudentAnswer.exam_id == exam.id).all()
    assert len(answers) == len(qids)
    assert all(a.diagnosis_source in ("rule", "error") for a in answers)
    assert all(a.diagnosis_version == "1.0" for a in answers)

    # 4. 复习同步：错题全部建 ReviewTask（pending 且创建即到期），到期列表可见
    tasks = db.query(ReviewTask).filter(ReviewTask.student_id == stu.id).all()
    assert len(tasks) == len(qids)
    assert all(t.status == ReviewTaskStatus.pending for t in tasks)
    assert all(t.next_review_at is not None for t in tasks)
    due = client.get(f"/api/review/tasks/{stu.id}", headers=headers).json()
    assert due["count"] == len(qids)

    # 5. 复习提交：连续答对 2 次升级 level2（先判正误再判级）
    task_id = tasks[0].id
    assert client.post("/api/review/submit", headers=headers,
                       json={"review_task_id": task_id, "passed": True}).json()["review_level"] == "level1"
    rep = client.post("/api/review/submit", headers=headers,
                      json={"review_task_id": task_id, "passed": True}).json()
    assert rep["review_level"] == "level2"

    # 6. 错题列表：按题去重 + 累计答错次数
    wrong = client.get(f"/api/wrong-questions/{stu.id}", headers=headers).json()
    assert wrong["count"] == len(qids)
    by_q = {i["question_id"]: i for i in wrong["items"]}
    assert all(by_q[qid]["error_count"] == 1 for qid in qids)

    # 7. 变式：同知识点同难度抽样，排除原题
    original = qids[0]
    variant = client.post("/api/wrong-questions/variants", headers=headers,
                          json={"question_id": original, "count": 1}).json()
    assert variant["questions"]
    assert variant["questions"][0]["content"] != exam.questions[0].content

    # 8. 训练会话：per-student 训练记录引用原题 id，逐题批改 + 答错复习同步（复用不重复）
    train = client.post("/api/wrong-questions/train", headers=headers,
                        json={"student_id": stu.id, "answers": [
                            {"question_id": qid, "selected_option": "x"} for qid in qids
                        ]}).json()
    rec = db.get(ExamRecord, train["exam_id"])
    assert rec.student_id == stu.id and rec.question_stats["mode"] == "training"
    assert train["question_count"] == len(qids)
    assert all(not r["is_correct"] for r in train["results"])
    assert db.query(ReviewTask).filter(ReviewTask.student_id == stu.id).count() == len(qids)

    # 9. 每日调度：同日去重跳过（不重复布置）
    summary = DailyPracticeScheduler(db).run_daily_batch(now=NOW)
    assert summary["created"] == 0 and summary["skipped"] == 1
    assert summary["failed"] == 0
