"""学生障碍画像聚合（doc 48 §6）。

把诊断链产出的 barrier_type 落库结果按学生聚合成三维画像 {concept, reading, expression}：
计数 → 归一化（分母为总数或 1，无作答不除零）→ 保留 2 位 → 补零保三键。
防御性归一化（T11）：既有 barrier_profile 为 NULL/缺键/畸形 JSON 时读路径补零三键不抛异常。
冻结语义（T1）：教师 override 后该学生画像被冻结，聚合跳过直到显式解除冻结。
"""
import math
from datetime import datetime

from app.db.models import Student, StudentAnswer

BARRIER_AXES = ("concept", "reading", "expression")
BARRIER_LABELS = {"concept": "概念理解", "reading": "审题障碍", "expression": "表述障碍"}


def aggregate(barrier_types: list[str | None]) -> dict[str, float]:
    """纯函数：统计障碍分布并归一化为 {concept, reading, expression}（保留 2 位）。"""
    counts = {k: 0 for k in BARRIER_AXES}
    for bt in barrier_types:
        if bt in counts:
            counts[bt] += 1
    total = sum(counts.values())
    if total == 0:
        return {k: 0.0 for k in BARRIER_AXES}
    return {k: round(v / total, 2) for k, v in counts.items()}


def normalize_profile(profile) -> dict[str, float]:
    """防御性归一化（T11）：NULL/缺键/畸形 JSON → 补零三键，不抛异常。"""
    if not isinstance(profile, dict):
        return {k: 0.0 for k in BARRIER_AXES}
    out: dict[str, float] = {}
    for k in BARRIER_AXES:
        v = profile.get(k)
        # NaN/inf 会静默污染 class_stats 平均值 → 一律归零（T11 防御扩展）
        out[k] = round(v, 2) if isinstance(v, (int, float)) and math.isfinite(v) else 0.0
    return out


def dominant_axis(profile) -> str:
    """障碍画像主导轴：归一化后取最高维；全零/空 → 默认 concept（与生成器/工具共用）。"""
    norm = normalize_profile(profile)
    if not any(v > 0 for v in norm.values()):
        return "concept"
    return max(norm, key=norm.get)


def refresh_profiles(session, student_ids: list[int] | None = None) -> int:
    """按学生分组聚合 barrier_type，更新 barrier_profile + barrier_last_updated。

    跳过 barrier_frozen=True 的冻结学生（T1）；返回更新条数。
    """
    query = session.query(StudentAnswer.student_id, StudentAnswer.barrier_type)
    if student_ids:
        query = query.filter(StudentAnswer.student_id.in_(student_ids))
    rows = query.all()

    groups: dict[int, list[str | None]] = {}
    for student_id, barrier_type in rows:
        groups.setdefault(student_id, []).append(barrier_type)

    frozen_ids = {
        sid for (sid,) in session.query(Student.id).filter(Student.barrier_frozen.is_(True))
    }
    now = datetime.utcnow()
    updated = 0
    for sid, bts in groups.items():
        if sid in frozen_ids:
            continue
        student = session.get(Student, sid)
        if student is None:
            continue
        student.barrier_profile = aggregate(bts)
        student.barrier_last_updated = now
        updated += 1
    session.commit()
    return updated
