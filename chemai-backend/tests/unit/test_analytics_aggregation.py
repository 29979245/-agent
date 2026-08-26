"""学情面板聚合纯函数测试（doc 31 §3，design.md D1-D4）。

覆盖：知识点错误率（一题多知识点/无作答/total=0 排除/降序）、均分指数衰减
（一周 50% 权重/单次异常低分不拉垮/空数据）、障碍主导计数（缺失/全零/常规）、
ClassLearningPanel 组装（排序/截断/空数据兜底）。
"""
from datetime import date, datetime

from app.services.analytics.aggregation import (
    TOP_ERRORS,
    TOP_KNOWLEDGE_POINTS,
    TREND_POINTS,
    build_class_panel,
    decay_weight,
    dominant_barrier_counts,
    knowledge_point_error_rates,
    weighted_exam_average,
)


# ---------------- 1.1 知识点错误率聚合 ----------------

def test_kp_error_rates_multi_kp_counted_per_kp():
    # 一题多知识点：一次答错对每个标记知识点各计一次 errors 与 total
    rows = [(1, False)]
    kp_map = {1: ["氧化还原", "化学平衡"]}
    assert knowledge_point_error_rates(rows, kp_map) == [
        {"knowledge_point": "氧化还原", "errors": 1, "total": 1, "error_rate": 100.0},
        {"knowledge_point": "化学平衡", "errors": 1, "total": 1, "error_rate": 100.0},
    ]


def test_kp_error_rates_mixed_correct_incorrect():
    rows = [(1, True), (1, False), (2, False)]
    kp_map = {1: ["氧化还原"], 2: ["离子反应"]}
    rates = knowledge_point_error_rates(rows, kp_map)
    assert {r["knowledge_point"]: r for r in rates} == {
        "氧化还原": {"knowledge_point": "氧化还原", "errors": 1, "total": 2, "error_rate": 50.0},
        "离子反应": {"knowledge_point": "离子反应", "errors": 1, "total": 1, "error_rate": 100.0},
    }


def test_kp_error_rates_sorted_desc_by_error_rate():
    rows = [(1, True), (2, False), (3, False)]
    kp_map = {1: ["A"], 2: ["B"], 3: ["C"]}
    kps = [r["knowledge_point"] for r in knowledge_point_error_rates(rows, kp_map)]
    assert kps == ["B", "C", "A"]  # B=100%, C=100% 并列按插入序，A=0% 末尾


def test_kp_error_rates_excludes_zero_total():
    # kp "D" 只在从未作答的题目上出现 → total=0 → 不参与排名
    rows = [(1, False)]
    kp_map = {1: ["A"], 99: ["D"]}
    assert [r["knowledge_point"] for r in knowledge_point_error_rates(rows, kp_map)] == ["A"]


def test_kp_error_rates_empty_rows():
    assert knowledge_point_error_rates([], {}) == []


# ---------------- 1.2 均分指数衰减加权 ----------------

def test_decay_weight_half_life_is_50_percent():
    assert decay_weight(0) == 1.0
    assert abs(decay_weight(604800) - 0.5) < 1e-9  # 一周前权重 50%


def test_weighted_average_all_equal_returns_accuracy():
    now = datetime(2026, 8, 26, 0, 0, 0)
    points = [(date(2026, 8, 26), 0.8), (date(2026, 8, 19), 0.8), (date(2026, 8, 12), 0.8)]
    assert abs(weighted_exam_average(points, now=now) - 0.8) < 1e-9


def test_weighted_average_anomaly_not_overweighted():
    now = datetime(2026, 8, 26, 0, 0, 0)
    # 一周前异常低分（0.1）权重仅 50% → 不会把整体均值过度拉低
    points = [(date(2026, 8, 26), 0.9), (date(2026, 8, 19), 0.1)]
    result = weighted_exam_average(points, now=now)
    assert result is not None
    assert abs(result - (0.9 + 0.1 * 0.5) / 1.5) < 1e-9
    assert result > 0.5  # 高于普通平均 0.5


def test_weighted_average_empty_is_none():
    assert weighted_exam_average([]) is None


def test_weighted_average_single_point():
    now = datetime(2026, 8, 26, 0, 0, 0)
    assert abs(weighted_exam_average([(date(2026, 8, 19), 0.6)], now=now) - 0.6) < 1e-9


# ---------------- 1.3 障碍分布主导计数 ----------------

def test_barrier_distribution_missing_or_zero_not_counted():
    counts = dominant_barrier_counts([None, {"concept": 0.0, "reading": 0.0, "expression": 0.0}])
    assert counts == {"concept": 0, "reading": 0, "expression": 0}


def test_barrier_distribution_dominant_count():
    counts = dominant_barrier_counts(
        [
            {"concept": 0.6, "reading": 0.2, "expression": 0.2},
            {"concept": 0.2, "reading": 0.7, "expression": 0.1},
            {"concept": 0.0, "reading": 0.3, "expression": 0.7},
            None,  # 不计入
        ]
    )
    assert counts == {"concept": 1, "reading": 1, "expression": 1}
    assert sum(counts.values()) == 3  # 等于有画像学生数


def test_barrier_distribution_tie_prefers_first_axis():
    counts = dominant_barrier_counts([{"concept": 0.5, "reading": 0.5, "expression": 0.0}])
    assert counts["concept"] == 1


# ---------------- 1.4 ClassLearningPanel 组装 ----------------

def _many_kps(n: int):
    rows, kp_map = [], {}
    for i in range(n):
        qid = i + 1
        kp_map[qid] = [f"KP{i}"]
        rows.append((qid, False))  # 全部答错 → 错误率 100%，按插入序排列
    return rows, kp_map


def test_build_class_panel_truncation_and_order():
    rows, kp_map = _many_kps(12)
    students = [
        {"concept": 0.8, "reading": 0.1, "expression": 0.1},
        {"concept": 0.0, "reading": 1.0, "expression": 0.0},
    ]
    exam_points = [(date(2026, 8, 1), 0.7), (date(2026, 8, 10), 0.5), (date(2026, 8, 20), 0.9)]
    panel = build_class_panel(
        class_id=1, class_name="高一1班", students=students,
        exam_points=exam_points, rows=rows, kp_map=kp_map,
    )

    assert len(panel["knowledge_points"]) == TOP_KNOWLEDGE_POINTS  # top10
    assert len(panel["top_errors"]) == TOP_ERRORS  # top5
    # 知识点评分全部 100% → 保持插入序，top10 为 KP0..KP9
    assert panel["knowledge_points"][0]["knowledge_point"] == "KP0"
    assert panel["knowledge_points"][-1]["knowledge_point"] == "KP9"
    assert panel["barrier_distribution"] == {"concept": 1, "reading": 1, "expression": 0}


def test_build_class_panel_trend_recent_10_ascending():
    exam_points = [(date(2026, 8, 1 + i), 0.5 + i / 100) for i in range(12)]
    panel = build_class_panel(
        class_id=1, class_name="高一1班", students=[],
        exam_points=exam_points, rows=[], kp_map={},
    )
    trend = panel["class_overview"]["avg_score_trend"]
    assert len(trend) == TREND_POINTS
    assert trend == sorted(trend, key=lambda p: p["exam_date"])  # 升序
    assert trend[0]["exam_date"] == "2026-08-03"  # 取最近 10 次，跳过前 2 次


def test_build_class_panel_empty_data():
    panel = build_class_panel(
        class_id=1, class_name="高一1班", students=[], exam_points=[], rows=[], kp_map={},
    )
    overview = panel["class_overview"]
    assert overview["exam_count"] == 0
    assert overview["avg_score_trend"] == []
    assert overview["recent_exam_avg"] is None
    assert overview["recent_exam_date"] is None
    assert overview["knowledge_mastery"] is None
    assert panel["knowledge_points"] == []
    assert panel["top_errors"] == []
    assert panel["barrier_distribution"] == {"concept": 0, "reading": 0, "expression": 0}


# ---------------- 1.5 知识点掌握率 ----------------

def test_build_class_panel_knowledge_mastery_percentage():
    # 3 次标记知识点作答，其中 2 次正确 → 掌握率 2/3 = 66.67%
    rows = [(1, True), (1, True), (2, False)]
    kp_map = {1: ["氧化还原"], 2: ["离子反应"]}
    panel = build_class_panel(
        class_id=1, class_name="高一1班", students=[],
        exam_points=[], rows=rows, kp_map=kp_map,
    )
    assert panel["class_overview"]["knowledge_mastery"] == 66.67


def test_build_class_panel_knowledge_mastery_all_correct():
    rows = [(1, True), (2, True)]
    kp_map = {1: ["氧化还原"], 2: ["离子反应"]}
    panel = build_class_panel(
        class_id=1, class_name="高一1班", students=[],
        exam_points=[], rows=rows, kp_map=kp_map,
    )
    assert panel["class_overview"]["knowledge_mastery"] == 100.0


def test_build_class_panel_knowledge_mastery_null_when_no_answers():
    panel = build_class_panel(
        class_id=1, class_name="高一1班", students=[],
        exam_points=[], rows=[], kp_map={},
    )
    assert panel["class_overview"]["knowledge_mastery"] is None
