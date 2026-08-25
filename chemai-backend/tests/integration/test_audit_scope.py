"""审核触发范围集成测试（设计 D7 / task 6.1）。

- AI 生成 → 两层审核（方程式级 + 题目级）
- 手动录入 / OCR 导入 → 只过方程式级硬闸（题目级跳过）
- 配平工具 → 只跑方程式级（check_balance / audit_equation）
"""
from app.services.audit.equation import audit_equation, check_balance
from app.services.audit.workflow import run_content_audit

BALANCED = "2H2 + O2 → 2H2O"
UNBALANCED = "H2 + O2 → H2O"

PASSED_REVIEW = (
    '{"scientificity": 90, "difficulty": 80, "knowledge": 85, "discrimination": 75}'
)
WARNING_REVIEW = (
    '{"scientificity": 75, "difficulty": 70, "knowledge": 75, "discrimination": 70}'
)


class _MockReviewClient:
    def __init__(self, response: str):
        self.response = response

    def complete(self, messages: list[dict]) -> str:
        return self.response


class _UnavailableReviewClient:
    """评审客户端不可用（未配置 llm_api_key 的降级信号）。"""

    available = False

    def complete(self, messages: list[dict]) -> str:
        raise AssertionError("不可用客户端不应被调用")


def test_ai_scope_runs_two_layers():
    """AI 生成走两层审核：方程式级 + 题目级都被执行。"""
    _, review, report = run_content_audit(
        BALANCED, "ai", review_client=_MockReviewClient(PASSED_REVIEW),
        question_data={"content": BALANCED},
    )
    assert review is not None
    assert report["question_level"] is not None
    assert report["question_level"]["composite"] == 84.25
    assert report["overall_status"] == "passed"


def test_ai_scope_warning_question_level_dominates():
    """题目级 warning 合成整体 warning（方程式级通过也救不回）。"""
    _, review, report = run_content_audit(
        BALANCED, "ai", review_client=_MockReviewClient(WARNING_REVIEW),
        question_data={"content": BALANCED},
    )
    assert review.status == "warning"
    assert report["overall_status"] == "warning"


def test_ai_scope_degrades_to_equation_when_llm_unavailable():
    """AI 来源但评审客户端不可用（无 llm_api_key）→ 降级方程式级硬闸，不 500。"""
    _, review, report = run_content_audit(
        BALANCED, "ai", review_client=_UnavailableReviewClient(),
        question_data={"content": BALANCED},
    )
    assert review is None
    assert report["question_level"] is None
    assert report["overall_status"] == "passed"  # 方程式级决定
    assert report["meta"]["review_note"] == "llm_unconfigured"


def test_manual_scope_skips_question_level():
    _, review, report = run_content_audit(BALANCED, "manual")
    assert review is None
    assert report["question_level"] is None
    assert report["overall_status"] == "passed"


def test_ocr_scope_skips_question_level():
    _, review, report = run_content_audit(UNBALANCED, "ocr")
    assert review is None
    assert report["question_level"] is None
    assert report["overall_status"] == "blocked"  # 未配平硬阻断


def test_balance_tool_only_equation_level():
    """配平工具只跑方程式级：check_balance 直通，不触发题目级。"""
    assert check_balance(BALANCED).status == "passed"
    assert check_balance(UNBALANCED).status == "blocked"


def test_balance_tool_uses_equation_audit():
    report = audit_equation(BALANCED)
    assert report.overall_status == "passed"
