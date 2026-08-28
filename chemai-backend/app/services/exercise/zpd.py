"""自适应练习的画像计算（doc 28 §5，design.md D6/D7）。

- ZPD 难度：最近 30 条作答正确率 → <40% easy / 40%-70% medium（含两端）/ >70% hard；冷启动 medium
- 薄弱知识点：全部错误作答按题目 knowledge_points 逐点累加，取 Top N（默认 3）
- 主导障碍：Student.barrier_profile 占比最高键，缺失/异常默认 concept
- 策略矩阵：concept 降低难度档，reading/expression 保持 ZPD 难度
"""
from collections import Counter
from typing import Optional

from app.db.models import Question, Student, StudentAnswer
from app.services.question.serializers import split_knowledge_points

ZPD_WINDOW = 30
ZPD_EASY_THRESHOLD = 0.4  # 正确率 < 40% → easy
ZPD_HARD_THRESHOLD = 0.7  # 正确率 > 70% → hard；40%-70%（含两端）→ medium
DEFAULT_BARRIER = "concept"
BARRIER_AXES = ("concept", "reading", "expression")

# 策略矩阵难度调整（design.md D7）：concept 降一档，reading/expression 保持
_DIFFICULTY_LOWER = {"hard": "medium", "medium": "easy", "easy": "easy", "competition": "hard"}


def compute_zpd_difficulty(db, student_id: int) -> str:
    """最近 ZPD_WINDOW 条作答正确率 → easy/medium/hard；无记录冷启动返回 medium。"""
    rows = (
        db.query(StudentAnswer)
        .filter(StudentAnswer.student_id == student_id)
        .order_by(StudentAnswer.answered_at.desc(), StudentAnswer.id.desc())
        .limit(ZPD_WINDOW)
        .all()
    )
    if not rows:
        return "medium"
    correct = sum(1 for a in rows if a.is_correct)
    rate = correct / len(rows)
    if rate < ZPD_EASY_THRESHOLD:
        return "easy"
    if rate <= ZPD_HARD_THRESHOLD:
        return "medium"
    return "hard"


def extract_weak_kps(db, student_id: int, top_n: int = 3) -> list[str]:
    """全部错误作答按题目知识点逐点累加，返回频次最高的前 top_n 个知识点。"""
    rows = (
        db.query(StudentAnswer)
        .filter(StudentAnswer.student_id == student_id, StudentAnswer.is_correct.is_(False))
        .all()
    )
    counts: Counter = Counter()
    for ans in rows:
        question = db.get(Question, ans.question_id)
        if question is None:
            continue
        for kp in split_knowledge_points(question.knowledge_points):
            counts[kp] += 1
    return [kp for kp, _ in counts.most_common(top_n)]


def dominant_barrier(student: Student) -> str:
    """障碍画像占比最高的障碍类型键；字段缺失/格式异常默认 concept。"""
    profile = getattr(student, "barrier_profile", None)
    if not isinstance(profile, dict) or not profile:
        return DEFAULT_BARRIER
    best_key: Optional[str] = None
    best_value = -1.0
    for key, value in profile.items():
        if not isinstance(value, (int, float)):
            continue
        if value > best_value:
            best_value = value
            best_key = key
    if best_key not in BARRIER_AXES:
        return DEFAULT_BARRIER
    return best_key


def adjust_difficulty(zpd_difficulty: str, barrier: str) -> str:
    """策略矩阵难度调整：concept 降一档，reading/expression 保持 ZPD 难度。"""
    if barrier == "concept":
        return _DIFFICULTY_LOWER.get(zpd_difficulty, zpd_difficulty)
    return zpd_difficulty


def fallback_kps_for(barrier: str) -> list[str]:
    """障碍 → 知识点映射（design.md D7）：无薄弱点/无错题时由调用方补足出题目标。"""
    _MAP = {
        "concept": ["氧化还原反应", "化学平衡", "离子反应"],
        "reading": ["化学实验", "化学计算"],
        "expression": ["化学用语", "化学方程式"],
    }
    return _MAP.get(barrier, _MAP[DEFAULT_BARRIER])


def resolve_knowledge_points(weak: list[str], barrier: str, top_n: int = 3) -> list[str]:
    """出题目标知识点：薄弱点不足 top_n 时用障碍映射补足，无薄弱点全用映射。

    spec adaptive-practice-engine：可用知识点不足 3 个时由调用方传入的知识点参数补足。
    """
    kps = list(weak)
    for kp in fallback_kps_for(barrier):
        if len(kps) >= top_n:
            break
        if kp not in kps:
            kps.append(kp)
    return kps
