"""自适应练习 + 间隔复习服务层单元测试（L1）。

覆盖：ZPD 边界值/冷启动、薄弱知识点一题多 KP、主导障碍识别、策略矩阵难度调整、
批次限制 5 人、抽样去重与不足兜底、艾宾浩斯间隔映射、升降级六路径、复习只写 ReviewHistory。
"""
import datetime

import pytest

from app.db.models import (
    Class,
    ExamRecord,
    Grade,
    Question,
    ReviewHistory,
    ReviewTask,
    School,
    Student,
    StudentAnswer,
)
from app.db.models.enums import Difficulty, ExamType, ReviewLevel, ReviewTaskStatus
from app.services.exercise.adaptive import AdaptivePracticeService
from app.services.exercise.sampling import sample_questions
from app.services.exercise.spaced_repetition import (
    SpacedRepetitionEngine,
    compute_next_review_at,
    interval_days,
    sync_review_tasks,
)
from app.services.exercise.zpd import (
    adjust_difficulty,
    compute_zpd_difficulty,
    dominant_barrier,
    extract_weak_kps,
    resolve_knowledge_points,
)


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
    exam = ExamRecord(class_id=None, student_id=None, name="base",
                      exam_type=ExamType.practice, exam_date=datetime.date.today())
    db_session.add(exam)
    db_session.flush()
    return {"class_id": cls.id, "exam_id": exam.id}


def _student(db_session, env, name="张三", profile=None):
    stu = Student(class_id=env["class_id"], name=name, barrier_profile=profile or {})
    db_session.add(stu)
    db_session.flush()
    return stu


def _question(db_session, kp="", difficulty=Difficulty.medium):
    q = Question(content="c", answer="a", knowledge_points=kp, difficulty=difficulty)
    db_session.add(q)
    db_session.flush()
    return q


def _answer(db_session, env, stu, q, correct, when=None):
    db_session.add(StudentAnswer(
        student_id=stu.id, question_id=q.id, exam_id=env["exam_id"],
        answer_text="a", is_correct=correct,
        answered_at=when or datetime.datetime.utcnow(),
    ))
    db_session.flush()


def _review_task(db_session, env, level=ReviewLevel.level1, status=ReviewTaskStatus.pending):
    stu = _student(db_session, env)
    q = _question(db_session)
    task = ReviewTask(student_id=stu.id, question_id=q.id, review_level=level, status=status,
                      next_review_at=datetime.datetime.utcnow())
    db_session.add(task)
    db_session.flush()
    return task


# ---- 2.1 ZPD 难度 ----

def test_zpd_cold_start_medium(db_session, env):
    stu = _student(db_session, env)
    assert compute_zpd_difficulty(db_session, stu.id) == "medium"


def test_zpd_boundary_40_percent_medium(db_session, env):
    stu = _student(db_session, env)
    q = _question(db_session)
    for i in range(10):
        _answer(db_session, env, stu, q, correct=(i < 4))  # 4/10 = 40%
    assert compute_zpd_difficulty(db_session, stu.id) == "medium"


def test_zpd_boundary_70_percent_medium(db_session, env):
    stu = _student(db_session, env)
    q = _question(db_session)
    for i in range(10):
        _answer(db_session, env, stu, q, correct=(i < 7))  # 7/10 = 70%
    assert compute_zpd_difficulty(db_session, stu.id) == "medium"


def test_zpd_below_40_easy(db_session, env):
    stu = _student(db_session, env)
    q = _question(db_session)
    for i in range(10):
        _answer(db_session, env, stu, q, correct=(i < 3))  # 30%
    assert compute_zpd_difficulty(db_session, stu.id) == "easy"


def test_zpd_above_70_hard(db_session, env):
    stu = _student(db_session, env)
    q = _question(db_session)
    for i in range(10):
        _answer(db_session, env, stu, q, correct=(i < 8))  # 80%
    assert compute_zpd_difficulty(db_session, stu.id) == "hard"


def test_zpd_window_is_30(db_session, env):
    """只取最近 30 条；更早的作答不影响。"""
    stu = _student(db_session, env)
    q = _question(db_session)
    for i in range(10):
        _answer(db_session, env, stu, q, correct=False, when=datetime.datetime(2026, 1, 1))
    for i in range(30):
        _answer(db_session, env, stu, q, correct=True, when=datetime.datetime(2026, 8, 1))
    assert compute_zpd_difficulty(db_session, stu.id) == "hard"


# ---- 2.2 薄弱知识点 ----

def test_weak_kp_multi_kp_count(db_session, env):
    """一道错题同时标记两个知识点 → 各自累加一次。"""
    stu = _student(db_session, env)
    q = _question(db_session, kp="氧化还原反应, 电子转移")
    _answer(db_session, env, stu, q, correct=False)
    assert extract_weak_kps(db_session, stu.id, top_n=3) == ["氧化还原反应", "电子转移"]


def test_weak_kp_top_n_and_empty(db_session, env):
    stu = _student(db_session, env)
    assert extract_weak_kps(db_session, stu.id) == []  # 无错题 → 空
    q1 = _question(db_session, kp="氧化还原反应")
    q2 = _question(db_session, kp="化学平衡")
    _answer(db_session, env, stu, q1, correct=False)
    _answer(db_session, env, stu, q1, correct=False)
    _answer(db_session, env, stu, q2, correct=False)
    assert extract_weak_kps(db_session, stu.id, top_n=3) == ["氧化还原反应", "化学平衡"]


def test_resolve_kp_pads_weak_with_fallback():
    """薄弱点不足 top_n 用障碍映射补足；无薄弱点全用映射（a2）。"""
    assert resolve_knowledge_points([], "concept") == ["氧化还原反应", "化学平衡", "离子反应"]
    assert resolve_knowledge_points(["离子反应"], "concept") == ["离子反应", "氧化还原反应", "化学平衡"]
    assert resolve_knowledge_points(["A", "B", "C"], "concept") == ["A", "B", "C"]  # 已满不补
    assert resolve_knowledge_points([], "reading") == ["化学实验", "化学计算"]  # 映射不足 3 取全量


def test_plan_for_pads_weak_kps(db_session, env):
    """plan_for 输出目标知识点 = 薄弱点 + 障碍映射补足（a2 闭环）。"""
    stu = _student(db_session, env, profile={"concept": 1.0})
    _question(db_session, kp="离子反应")
    _answer(db_session, env, stu, db_session.query(Question).first(), correct=False)
    plan = AdaptivePracticeService(db_session).plan_for(stu)
    assert plan["barrier"] == "concept"
    assert plan["weak_kps"] == ["离子反应"]
    assert plan["knowledge_points"] == ["离子反应", "氧化还原反应", "化学平衡"]


# ---- 2.3 主导障碍 ----

def test_dominant_barrier_highest(db_session, env):
    stu = _student(db_session, env, profile={"concept": 0.7, "reading": 0.15, "expression": 0.15})
    assert dominant_barrier(stu) == "concept"


def test_dominant_barrier_default_on_missing(db_session, env):
    assert dominant_barrier(_student(db_session, env)) == "concept"
    assert dominant_barrier(_student(db_session, env, profile={"bad": 1.0})) == "concept"
    assert dominant_barrier(_student(db_session, env, profile={"reading": None})) == "concept"


# ---- 策略矩阵 ----

def test_adjust_difficulty_concept_lower():
    assert adjust_difficulty("hard", "concept") == "medium"
    assert adjust_difficulty("medium", "concept") == "easy"
    assert adjust_difficulty("easy", "concept") == "easy"


def test_adjust_difficulty_keep_for_reading_expression():
    assert adjust_difficulty("hard", "reading") == "hard"
    assert adjust_difficulty("medium", "expression") == "medium"


# ---- 2.5 批次限制 ----

def test_generate_batch_limit_5(db_session, env, tmp_path):
    from app.services.question.historical import reload_bank
    p = tmp_path / "全国卷" / "2024"
    p.mkdir(parents=True, exist_ok=True)
    (p / "真题.json").write_text(
        '{"questions": [{"id": "q1", "content": "真题A", "answer": "B", '
        '"knowledge_points": ["氧化还原反应"], "difficulty": "easy"}]}',
        encoding="utf-8",
    )
    bank = reload_bank(tmp_path)
    students = [_student(db_session, env, name=f"学生{i}", profile={"concept": 1.0}) for i in range(6)]
    svc = AdaptivePracticeService(db_session, bank=bank)
    result = svc.generate_batch([s.id for s in students], count=3)
    assert result["remaining"] == 1  # 6 人超 1 人
    assert len(result["results"]) == 5  # 仅处理前 5
    assert result["batch_limit"] == 5
    created = db_session.query(ExamRecord).filter(ExamRecord.student_id.isnot(None)).count()
    assert created == 5  # 每位学生一份独立练习记录


# ---- 抽样 ----

def test_sample_questions_excludes_and_shortfall(tmp_path):
    from app.services.question.historical import reload_bank
    p = tmp_path / "全国卷" / "2024"
    p.mkdir(parents=True, exist_ok=True)
    (p / "真题.json").write_text(
        '{"questions": ['
        '{"id": "q1", "content": "A", "answer": "B", "knowledge_points": ["氧化还原反应"], "difficulty": "easy"},'
        '{"id": "q2", "content": "B", "answer": "C", "knowledge_points": ["氧化还原反应"], "difficulty": "medium"},'
        '{"id": "q3", "content": "C", "answer": "D", "knowledge_points": ["化学平衡"], "difficulty": "easy"}'
        "]}",
        encoding="utf-8",
    )
    bank = reload_bank(tmp_path)
    selected, shortfall = sample_questions(
        bank, ["氧化还原反应"], "medium", count=3, exclude_ref_ids=("全国卷/2024/真题#q1",),
    )
    assert [ref for ref, _ in selected] == ["全国卷/2024/真题#q2"]
    assert shortfall == 2  # 仅命中 1 道


def test_sample_questions_choice_only_filters_non_option(tmp_path):
    """choice_only=True 只抽带选项的选择题（学生端答题按选项作答）。"""
    from app.services.question.historical import reload_bank
    p = tmp_path / "全国卷" / "2024"
    p.mkdir(parents=True, exist_ok=True)
    (p / "真题.json").write_text(
        '{"questions": ['
        '{"id": "q1", "content": "A", "answer": "B", "options": ["A", "B", "C", "D"], "knowledge_points": ["氧化还原反应"], "difficulty": "easy"},'
        '{"id": "q2", "content": "B", "answer": "C", "knowledge_points": ["氧化还原反应"], "difficulty": "easy"}'
        "]}",
        encoding="utf-8",
    )
    bank = reload_bank(tmp_path)
    choice_only, _ = sample_questions(bank, ["氧化还原反应"], "easy", count=5, choice_only=True)
    assert [ref for ref, _ in choice_only] == ["全国卷/2024/真题#q1"]  # 跳过无选项的 q2
    mixed, _ = sample_questions(bank, ["氧化还原反应"], "easy", count=5, choice_only=False)
    assert len(mixed) == 2  # 默认包含非选择题


# ---- 3.1 艾宾浩斯间隔映射 ----

def test_review_interval_mapping():
    assert interval_days(ReviewLevel.level1) == 1
    assert interval_days(ReviewLevel.level2) == 3
    assert interval_days(ReviewLevel.level3) == 7
    assert interval_days(ReviewLevel.level4) == 14
    assert interval_days(ReviewLevel.level5) == 30
    assert interval_days(ReviewLevel.level6) is None  # 不再安排


def test_compute_next_review_at():
    now = datetime.datetime(2026, 8, 26, 8, 0, 0)
    assert compute_next_review_at(ReviewLevel.level1, now) == now + datetime.timedelta(days=1)
    assert compute_next_review_at(ReviewLevel.level6, now) is None


# ---- 3.2 升降级状态机 ----

def test_upgrade_after_two_consecutive_correct(db_session, env):
    engine = SpacedRepetitionEngine(db_session)
    task = _review_task(db_session, env, level=ReviewLevel.level1)
    engine.apply_review(task, passed=True)
    assert task.review_level == ReviewLevel.level1  # 连续答对 1 次不升级
    engine.apply_review(task, passed=True)
    assert task.review_level == ReviewLevel.level2  # 连续答对 2 次升级
    assert task.status == ReviewTaskStatus.pending
    assert task.next_review_at is not None
    hist = db_session.query(ReviewHistory).filter_by(review_task_id=task.id).all()
    assert len(hist) == 2 and all(h.passed for h in hist)


def test_slip_exemption_no_downgrade(db_session, env):
    """上次连续答对 1 次，本次答错 → 回落豁免不降级。"""
    engine = SpacedRepetitionEngine(db_session)
    task = _review_task(db_session, env, level=ReviewLevel.level3)
    engine.apply_review(task, passed=True)   # consecutive_correct=1
    assert task.review_level == ReviewLevel.level3
    engine.apply_review(task, passed=False)  # 答错，回落豁免
    assert task.review_level == ReviewLevel.level3
    assert task.consecutive_correct == 0


def test_level1_floor_no_downgrade(db_session, env):
    engine = SpacedRepetitionEngine(db_session)
    task = _review_task(db_session, env, level=ReviewLevel.level1)
    engine.apply_review(task, passed=False)
    assert task.review_level == ReviewLevel.level1  # 保底
    assert task.status == ReviewTaskStatus.pending


def test_downgrade_on_error(db_session, env):
    engine = SpacedRepetitionEngine(db_session)
    task = _review_task(db_session, env, level=ReviewLevel.level4)
    engine.apply_review(task, passed=False)  # 无回落豁免、非首级 → 降 1 级
    assert task.review_level == ReviewLevel.level3


def test_level6_reached_is_done(db_session, env):
    engine = SpacedRepetitionEngine(db_session)
    task = _review_task(db_session, env, level=ReviewLevel.level5)
    engine.apply_review(task, passed=True)   # cc=1
    engine.apply_review(task, passed=True)   # cc=2 → level6 → done
    assert task.review_level == ReviewLevel.level6
    assert task.status == ReviewTaskStatus.done
    assert task.next_review_at is None
    assert task.completed_at is not None


def test_review_writes_only_history(db_session, env):
    """复习提交只写 ReviewHistory，不写 StudentAnswer。"""
    engine = SpacedRepetitionEngine(db_session)
    task = _review_task(db_session, env, level=ReviewLevel.level1)
    engine.apply_review(task, passed=True)
    assert db_session.query(StudentAnswer).count() == 0
    assert db_session.query(ReviewHistory).count() == 1


# ---- 复习同步去重 ----

def test_sync_review_tasks_dedup(db_session, env):
    stu = _student(db_session, env)
    q = _question(db_session)
    now = datetime.datetime(2026, 8, 26, 8, 0, 0)
    assert sync_review_tasks(db_session, stu.id, [q.id], now=now) == 1
    assert sync_review_tasks(db_session, stu.id, [q.id], now=now) == 0  # 去重
    task = db_session.query(ReviewTask).filter_by(student_id=stu.id, question_id=q.id).first()
    assert task.next_review_at == now  # 创建即到期
    assert task.review_level == ReviewLevel.level1
