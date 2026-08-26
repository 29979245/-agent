"""种子脚本：为 student_demo 生成练习 / 错题 / 复习 三页演示数据。

用法（在 chemai-backend/ 下）：
    python scripts/seed_student_demo.py

生成数据：
- 今日「每日练习」一份（pending，未作答）→ 练习页「待完成」
- 昨日「每日练习」一份（已作答，含 2 题答错）→ 练习页「已完成」+ 错题页 + 复习页
  （答错题经 sync_review_tasks 落 ReviewTask，复习页显示「首次学习」待复习）

说明：真题库 data/exam_bank 仅 2 道填空题（无选项），无法支撑选择题作答演示，
故练习题目由本脚本内置的单选题种直接落库（source=practice），保证前端可作答。

脚本幂等：账号/练习记录/作答已存在则跳过，重复执行安全。
"""
import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.security import hash_password
from app.db.models import (
    Account,
    Class as ClassModel,
    ExamRecord,
    ExamStatus,
    ExamType,
    Grade,
    Question,
    ReviewTask,
    School,
    Student,
    StudentAnswer,
    Teacher,
)
from app.db.models.enums import (
    AccountRole,
    AuditStatus,
    Difficulty,
    QuestionSource,
    TeacherStatus,
    TeacherSubRole,
)
from app.db.session import SessionLocal
from app.services.exercise.spaced_repetition import sync_review_tasks

DAILY_NAME = "每日练习"
TODAY_SPECS_INDEX = 3  # 今日练习取前 3 道，昨日取后 3 道

DEMO_QUESTIONS = [
    {
        "content": "下列反应中，水既作氧化剂又作还原剂的是？",
        "options": ["2H₂O = 2H₂↑ + O₂↑", "2Na + 2H₂O = 2NaOH + H₂↑", "Cl₂ + H₂O = HCl + HClO", "SO₃ + H₂O = H₂SO₄"],
        "answer": "2H₂O = 2H₂↑ + O₂↑",
        "analysis": "水分解时氢元素被还原、氧元素被氧化，水同时作氧化剂与还原剂；2Na + 2H₂O 中水只作氧化剂，Cl₂ + H₂O 中水既不作氧化剂也不作还原剂。",
        "knowledge_points": "氧化还原反应",
        "difficulty": "easy",
    },
    {
        "content": "下列离子方程式书写正确的是？",
        "options": [
            "盐酸与氢氧化钠反应：H⁺ + OH⁻ = H₂O",
            "铁与稀硫酸反应：2Fe + 6H⁺ = 2Fe³⁺ + 3H₂↑",
            "碳酸钙与盐酸反应：CO₃²⁻ + 2H⁺ = CO₂↑ + H₂O",
            "铜与硝酸银反应：Cu + Ag⁺ = Cu²⁺ + Ag",
        ],
        "answer": "盐酸与氢氧化钠反应：H⁺ + OH⁻ = H₂O",
        "analysis": "铁与稀硫酸反应生成 Fe²⁺ 而非 Fe³⁺；碳酸钙难溶不能拆写为 CO₃²⁻；电荷不守恒，应为 Cu + 2Ag⁺ = Cu²⁺ + 2Ag。",
        "knowledge_points": "离子反应",
        "difficulty": "easy",
    },
    {
        "content": "对反应 N₂ + 3H₂ ⇌ 2NH₃，下列措施能提高 H₂ 转化率的是？",
        "options": ["增大压强", "升高温度", "加入催化剂", "恒容充入惰性气体"],
        "answer": "增大压强",
        "analysis": "该反应是气体分子数减小的反应，增大压强平衡向正方向移动，H₂ 转化率提高；升高温度平衡向吸热的逆方向移动，催化剂不改变化学平衡。",
        "knowledge_points": "化学平衡",
        "difficulty": "medium",
    },
    {
        "content": "在无色溶液中，下列离子能大量共存的是？",
        "options": ["K⁺、Na⁺、SO₄²⁻、MnO₄⁻", "H⁺、Ba²⁺、Cl⁻、CO₃²⁻", "Na⁺、Ca²⁺、Cl⁻、NO₃⁻", "Fe³⁺、K⁺、Cl⁻、NO₃⁻"],
        "answer": "Na⁺、Ca²⁺、Cl⁻、NO₃⁻",
        "analysis": "MnO₄⁻ 紫红色、Fe³⁺ 黄色均不符合无色要求；H⁺ 与 CO₃²⁻ 不能大量共存。",
        "knowledge_points": "离子反应",
        "difficulty": "easy",
    },
    {
        "content": "相同状况下，下列气体密度最大的是？",
        "options": ["H₂", "O₂", "Cl₂", "CO₂"],
        "answer": "Cl₂",
        "analysis": "同温同压下气体密度与摩尔质量成正比，Cl₂ 摩尔质量 71 g/mol 最大，故密度最大。",
        "knowledge_points": "物质的量",
        "difficulty": "easy",
    },
    {
        "content": "将铁片投入下列溶液中，溶液质量会减小的是？",
        "options": ["CuSO₄ 溶液", "稀硫酸", "Fe₂(SO₄)₃ 溶液", "稀盐酸"],
        "answer": "CuSO₄ 溶液",
        "analysis": "Fe 置换 Cu，进入溶液的 Fe（56）少于析出的 Cu（64），溶液质量减小；与酸反应放出气体，Fe 进入使溶液质量增加。",
        "knowledge_points": "金属及其化合物",
        "difficulty": "medium",
    },
]


def _get_or_create(db, model, **kwargs):
    defaults = kwargs.pop("defaults", {})
    obj = db.query(model).filter_by(**kwargs).first()
    if obj is not None:
        return obj, False
    obj = model(**kwargs, **defaults)
    db.add(obj)
    db.flush()
    return obj, True


def _latest_daily(db, student_id, day):
    rows = (
        db.query(ExamRecord)
        .filter(
            ExamRecord.student_id == student_id,
            ExamRecord.exam_type == ExamType.practice,
            ExamRecord.exam_date == day,
        )
        .all()
    )
    for e in rows:
        if (e.question_stats or {}).get("mode") == "daily":
            return e
    return None


def _ensure_daily(db, student, day_dt):
    """确保存在某日 mode=daily 的每日练习记录；返回 (exam, created)。"""
    day = day_dt.date()
    exam = _latest_daily(db, student.id, day)
    if exam is not None:
        return exam, False
    exam = ExamRecord(
        class_id=None,
        student_id=student.id,
        name=DAILY_NAME,
        exam_type=ExamType.practice,
        status=ExamStatus.published,
        exam_date=day,
        question_stats={
            "mode": "daily",
            "difficulty": "easy",
            "zpd_difficulty": "medium",
            "barrier": "concept",
            "deadline": (day + datetime.timedelta(days=1)).isoformat(),
        },
    )
    db.add(exam)
    db.flush()
    return exam, True


def _ensure_questions(db, exam_id, specs):
    """练习记录无题目时按内置题种落库；已有则跳过。"""
    existing = db.query(Question.id).filter(Question.record_id == exam_id).first()
    if existing is not None:
        return
    for spec in specs:
        db.add(Question(
            content=spec["content"],
            options=spec["options"],
            answer=spec["answer"],
            analysis=spec["analysis"],
            knowledge_points=spec["knowledge_points"],
            difficulty=Difficulty(spec["difficulty"]),
            source=QuestionSource.practice,
            audit_status=AuditStatus.passed,
            audit_report={},
            record_id=exam_id,
        ))
    db.flush()


def _wrong_option(q):
    opts = [o for o in (q.options or []) if o != q.answer]
    return opts[0] if opts else q.answer


def _ensure_answers(db, student_id, exam_id, answered_at=None):
    """练习未作答时写一份混合作答（错/对交替），答错题同步复习任务；已作答跳过。"""
    already = db.query(StudentAnswer.id).filter(StudentAnswer.exam_id == exam_id).first()
    if already is not None:
        return 0
    questions = (
        db.query(Question).filter(Question.record_id == exam_id).order_by(Question.id).all()
    )
    if not questions:
        return 0
    answered_at = answered_at or datetime.datetime.utcnow()
    wrong_ids = []
    for i, q in enumerate(questions):
        is_correct = (i % 2 == 1)  # 错/对/错… 制造错题
        answer_text = q.answer if is_correct else _wrong_option(q)
        if not is_correct:
            wrong_ids.append(q.id)
        db.add(StudentAnswer(
            student_id=student_id,
            question_id=q.id,
            exam_id=exam_id,
            answer_text=answer_text,
            is_correct=is_correct,
            answered_at=answered_at,
        ))
    if wrong_ids:
        sync_review_tasks(db, student_id, wrong_ids)
    db.flush()
    return len(wrong_ids)


def main() -> None:
    db = SessionLocal()
    try:
        school, _ = _get_or_create(
            db, School, name="智辅化学演示学校",
            defaults={"region": "本地演示", "phone": "010-00000000",
                      "current_semester": "2026-2027 学年"},
        )
        teacher, _ = _get_or_create(
            db, Teacher, phone="13800000001",
            defaults={"school_id": school.id, "name": "演示教师",
                      "status": TeacherStatus.approved,
                      "sub_role": TeacherSubRole.teacher},
        )
        _get_or_create(
            db, Account, username="teacher_demo",
            defaults={"password_hash": hash_password("demo123"),
                      "role": AccountRole.teacher, "role_id": teacher.id},
        )
        grade, _ = _get_or_create(
            db, Grade, school_id=school.id, name="高一",
            defaults={"academic_year": "2026-2027"},
        )
        cls, _ = _get_or_create(
            db, ClassModel, grade_id=grade.id, name="高一（3）班",
            defaults={"subject": "化学", "head_teacher": teacher.name},
        )
        student, _ = _get_or_create(
            db, Student, class_id=cls.id, name="演示学生", defaults={"bind_code": "000000"},
        )
        _get_or_create(
            db, Account, username="student_demo",
            defaults={"password_hash": hash_password("demo123"),
                      "role": AccountRole.student, "role_id": student.id},
        )

        now = datetime.datetime.utcnow()
        today_exam, today_created = _ensure_daily(db, student, now)
        _ensure_questions(db, today_exam.id, DEMO_QUESTIONS[:TODAY_SPECS_INDEX])

        yesterday = now - datetime.timedelta(days=1)
        y_exam, y_created = _ensure_daily(db, student, yesterday)
        _ensure_questions(db, y_exam.id, DEMO_QUESTIONS[TODAY_SPECS_INDEX:])
        wrong = _ensure_answers(db, student.id, y_exam.id, answered_at=yesterday)

        db.commit()

        today_q = db.query(Question).filter(Question.record_id == today_exam.id).count()
        y_q = db.query(Question).filter(Question.record_id == y_exam.id).count()
        review_n = db.query(ReviewTask).filter(ReviewTask.student_id == student.id).count()
        print("student_demo 演示数据就绪：")
        print(f"  今日每日练习  pending  exam_id={today_exam.id} 题目={today_q}  {'(新建)' if today_created else '(已存在)'}")
        print(f"  昨日每日练习  completed exam_id={y_exam.id} 题目={y_q} 答错={wrong}  {'(新建)' if y_created else '(已存在)'}")
        print(f"  复习任务 {review_n} 条  错题见错题本页")
    finally:
        db.close()


if __name__ == "__main__":
    main()
