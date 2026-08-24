"""合成/阈值判定评测用例（task 3.3/6.2，L1/L2 层）。

固定输入（mock LLM 分数 + 两层状态交叉），只测合成纯函数，不测 LLM 本身。
供 `run_evals --tier l1/l2` 与 pytest 评测共用。
"""
from app.services.audit.review import classify_review
from app.services.audit.workflow import compose_overall_status

# 两层状态 9 全交叉 + 题目级缺失 3 例
COMPOSE_CASES = [
    ("passed", "passed", "passed"),
    ("passed", "warning", "warning"),
    ("passed", "blocked", "blocked"),
    ("warning", "passed", "warning"),
    ("warning", "warning", "warning"),
    ("warning", "blocked", "blocked"),
    ("blocked", "passed", "blocked"),
    ("blocked", "warning", "blocked"),
    ("blocked", "blocked", "blocked"),
    ("passed", None, "passed"),
    ("warning", None, "warning"),
    ("blocked", None, "blocked"),
]

# 综合分阈值 + 科学性红线边界
CLASSIFY_CASES = [
    (84.25, 90, "passed"),
    (80.0, 80, "passed"),
    (79.99, 80, "warning"),
    (70.0, 80, "warning"),
    (69.99, 80, "blocked"),
    (90.0, 65, "blocked"),   # 科学性 <70 硬阻断
    (100.0, 70, "passed"),   # 科学性恰好 70 不阻断
    (0.0, 0.0, "blocked"),
]


def _correct_rate(cases, fn) -> float:
    wrong = sum(1 for *args, expected in cases if fn(*args) != expected)
    return 1 - wrong / len(cases)


def composite_correctness() -> dict[str, float]:
    """L1/L2 合成正确率：返回 {指标: 通过率}。"""
    return {
        "compose_overall_status": _correct_rate(COMPOSE_CASES, compose_overall_status),
        "classify_review": _correct_rate(CLASSIFY_CASES, lambda c, s: classify_review(c, s)[0]),
    }
