"""学情面板聚合纯函数（doc 31 §3，design.md D1-D4）。

聚合策略：批量 SQL 拉取班级作答与题目知识点映射后，全部在 Python 内存聚合
（单班 ≤50 学生 × ≤千级作答，避免 SQLite/MySQL 方言差异，逻辑集中可单测）。
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, datetime
from typing import Iterable

from app.services.diagnosis.aggregation import BARRIER_AXES, normalize_profile

# 指数衰减半衰期：一周（秒），一周前数据权重 50%（design.md D3）
DECAY_HALF_LIFE_SECONDS = 604800
TOP_KNOWLEDGE_POINTS = 10
TOP_ERRORS = 5
TREND_POINTS = 10


def knowledge_point_error_rates(
    rows: Iterable[tuple[int, bool]], kp_map: dict[int, list[str]]
) -> list[dict]:
    """知识点错误率聚合：E(kp) = errors / total（一题多知识点分别计）。

    rows 为 (question_id, is_correct) 可迭代；kp_map 为预构建的 question_id → 知识点列表。
    total=0 的知识点不参与降序排名（spec「知识点从未被练习」）。
    """
    total: dict[str, int] = defaultdict(int)
    errors: dict[str, int] = defaultdict(int)
    for question_id, is_correct in rows:
        for kp in kp_map.get(question_id, ()):
            total[kp] += 1
            if not is_correct:
                errors[kp] += 1
    items = []
    for kp, t in total.items():
        if t <= 0:
            continue
        e = errors[kp]
        items.append(
            {
                "knowledge_point": kp,
                "errors": e,
                "total": t,
                "error_rate": round(e / t * 100, 2),
            }
        )
    items.sort(key=lambda x: x["error_rate"], reverse=True)
    return items


def decay_weight(age_seconds: float, half_life: float = DECAY_HALF_LIFE_SECONDS) -> float:
    """指数衰减权重：w = exp(-ln2 · Δt / 半衰期)，一周前数据权重 50%（design.md D3）。"""
    return math.exp(-math.log(2) * age_seconds / half_life)


def _as_datetime(value: date | datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.combine(value, datetime.min.time())


def weighted_exam_average(
    points: Iterable[tuple[date | datetime, float]],
    now: datetime | None = None,
) -> float | None:
    """班级均分指数衰减加权：Avg = SUM(w_i · acc_i) / SUM(w_i)。

    points 为 (时间戳, 正确率 0-1) 可迭代；now 为衰减基准（默认当前 UTC 时间）。
    无有效数据返回 None。单次异常低分因权重衰减不会过度拉低均值。
    """
    now = now or datetime.utcnow()
    num = 0.0
    den = 0.0
    for ts, accuracy in points:
        if accuracy is None:
            continue
        age = max((now - _as_datetime(ts)).total_seconds(), 0.0)
        w = decay_weight(age)
        num += w * accuracy
        den += w
    if den <= 0:
        return None
    return num / den


def dominant_barrier_counts(profiles: Iterable[dict | None]) -> dict[str, int]:
    """障碍分布主导计数（design.md D4 / spec「障碍分布按主导障碍计数」）。

    对每个画像取 normalize_profile 后占比最高且 >0 的维度归入三类计数；
    画像缺失/全零学生不计入。返回三类整数人数。
    """
    counts = {k: 0 for k in BARRIER_AXES}
    for profile in profiles:
        norm = normalize_profile(profile)
        if sum(norm.values()) <= 0:
            continue
        dominant = max(norm, key=norm.get)
        counts[dominant] += 1
    return counts


def build_class_panel(
    *,
    class_id: int,
    class_name: str,
    students: Iterable[dict | None],
    exam_points: Iterable[tuple[date | datetime, float]],
    rows: Iterable[tuple[int, bool]],
    kp_map: dict[int, list[str]],
    now: datetime | None = None,
) -> dict:
    """ClassLearningPanel 组装（spec「班级学情面板完整数据」，design.md D6）。

    输入全部为纯数据：students 为学生 barrier_profile 列表，exam_points 为
    (exam_date, 正确率) 序列（升序），rows 为 (question_id, is_correct) 作答，
    kp_map 为题目知识点映射。
    """
    exam_points = sorted(
        (_as_datetime(ts), acc) for ts, acc in exam_points if acc is not None
    )
    student_profiles = list(students)
    trend = [
        {"exam_date": ts.date().isoformat(), "avg_score": round(acc * 100, 2)}
        for ts, acc in exam_points[-TREND_POINTS:]
    ]
    weighted = weighted_exam_average(exam_points, now=now)
    last_exam = exam_points[-1][0].date() if exam_points else None

    kp_rates = knowledge_point_error_rates(rows, kp_map)
    kp_total = sum(item["total"] for item in kp_rates)
    kp_errors = sum(item["errors"] for item in kp_rates)
    kp_mastery = (
        round((kp_total - kp_errors) / kp_total * 100, 2) if kp_total > 0 else None
    )
    return {
        "class_overview": {
            "class_id": class_id,
            "class_name": class_name,
            "total_students": len(student_profiles),
            "exam_count": len(exam_points),
            "avg_score_trend": trend,
            "recent_exam_avg": round(weighted * 100, 2) if weighted is not None else None,
            "recent_exam_date": last_exam.isoformat() if last_exam else None,
            "knowledge_mastery": kp_mastery,
        },
        "knowledge_points": kp_rates[:TOP_KNOWLEDGE_POINTS],
        "top_errors": kp_rates[:TOP_ERRORS],
        "barrier_distribution": dominant_barrier_counts(student_profiles),
        "top_improvers": [],
        "top_declining": [],
    }
