"""双层合成与 audit_report 结构测试（设计 D3/D4，task 3.1/3.2）。"""
from app.db.models.enums import Difficulty
from app.db.models.exam import Question
from app.services.audit.equation import audit_equation
from app.services.audit.review import QuestionReviewResult, ReviewScores
from app.services.audit.workflow import (
    build_audit_report,
    compose_overall_status,
)


def _passed_review() -> QuestionReviewResult:
    return QuestionReviewResult(
        scores=ReviewScores(scientificity=90, difficulty=80, knowledge=85, discrimination=75),
        composite=84.25,
        status="passed",
        reason="综合分 ≥80，题目级通过",
    )


# ---- 双层合成纯函数（3.1）----


def test_compose_passed_passed():
    assert compose_overall_status("passed", "passed") == "passed"


def test_compose_any_blocked_wins():
    assert compose_overall_status("passed", "blocked") == "blocked"
    assert compose_overall_status("blocked", "passed") == "blocked"
    assert compose_overall_status("blocked", "blocked") == "blocked"
    assert compose_overall_status("blocked", "warning") == "blocked"
    assert compose_overall_status("warning", "blocked") == "blocked"


def test_compose_any_warning_when_no_blocked():
    assert compose_overall_status("passed", "warning") == "warning"
    assert compose_overall_status("warning", "passed") == "warning"
    assert compose_overall_status("warning", "warning") == "warning"


def test_compose_no_question_level_uses_equation_status():
    assert compose_overall_status("passed", None) == "passed"
    assert compose_overall_status("warning", None) == "warning"
    assert compose_overall_status("blocked", None) == "blocked"


# ---- build_audit_report（3.2）----


def test_report_full_structure():
    eq = audit_equation("2H2 + O2 → 2H2O")  # 方程式级 passed
    report = build_audit_report(eq, _passed_review())
    assert report["overall_status"] == "passed"
    # equation_level 四个中文维度 + overall_status
    assert set(report["equation_level"]) >= {
        "系数配平", "反应条件", "产物正确性", "分子结构", "overall_status",
    }
    assert report["equation_level"]["系数配平"]["status"] == "passed"
    assert "evidence" in report["equation_level"]["反应条件"]
    # question_level 四个中文维度 + composite + status + reason
    assert report["question_level"]["科学性"]["score"] == 90
    assert report["question_level"]["难度匹配"]["score"] == 80
    assert report["question_level"]["composite"] == 84.25
    assert report["question_level"]["status"] == "passed"
    assert report["meta"] == {"regeneration_attempts": 0, "generation_failed": False}


def test_report_equation_only_no_question_level():
    eq = audit_equation("2H2 + O2 → 2H2O")
    report = build_audit_report(eq)
    assert report["question_level"] is None
    assert report["overall_status"] == "passed"


def test_report_equation_blocked_dominates():
    eq = audit_equation("H2 + O2 → H2O")  # 未配平 → blocked
    report = build_audit_report(eq, _passed_review())
    assert report["overall_status"] == "blocked"
    assert report["equation_level"]["overall_status"] == "blocked"


def test_report_meta_custom_passthrough():
    eq = audit_equation("2H2 + O2 → 2H2O")
    report = build_audit_report(
        eq, _passed_review(), meta={"regeneration_attempts": 2, "generation_failed": True}
    )
    assert report["meta"]["regeneration_attempts"] == 2
    assert report["meta"]["generation_failed"] is True


def test_report_roundtrip_in_question_model(db_session):
    q = Question(
        content="配平：H2 + O2 → H2O",
        answer="2H2 + O2 → 2H2O",
        difficulty=Difficulty.medium,
        audit_report={},
    )
    db_session.add(q)
    db_session.flush()

    eq = audit_equation("2H2 + O2 → 2H2O")
    report = build_audit_report(eq, _passed_review())
    q.audit_report = report
    db_session.commit()

    loaded = db_session.get(Question, q.id)
    assert loaded.audit_report["overall_status"] == "passed"
    assert loaded.audit_report["question_level"]["composite"] == 84.25
    assert loaded.audit_report["equation_level"]["系数配平"]["status"] == "passed"
    assert loaded.audit_report["meta"] == {"regeneration_attempts": 0, "generation_failed": False}
