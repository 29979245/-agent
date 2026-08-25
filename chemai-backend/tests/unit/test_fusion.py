"""置信度融合引擎测试（doc 48 §5 + 评审 T5 单侧打标 / T6 双高冲突 flag）。

覆盖：双高一致融合、规则高置信冲突以规则为准、LLM 高置信冲突以 LLM 为准、
双低标记人工审核、仅规则/仅 LLM 折算、标签边界、补救去重、单侧高置信标签、
双高冲突 manual_review、LLM 错误降级单侧、双缺 error。
"""
import pytest

from app.services.diagnosis.fusion import (
    LLM_WEIGHT,
    RULE_WEIGHT,
    fuse,
    _clamp01,
    _label,
    _normalize_weights,
)
from app.services.diagnosis.llm_diagnosis import DiagnosisResult, error_signal
from app.services.diagnosis.rule_engine import RuleMatch


def _rule(barrier="concept", conf=0.9, rems=("复习勒夏特列", "回顾升温方向")):
    return RuleMatch(
        rule_id="RULE_001", category="化学平衡", name="勒夏特列方向混淆",
        barrier_type=barrier, confidence=conf, severity=3,
        remediation_points=rems, related_questions=("RULE_002",),
    )


def _llm(barrier="concept", conf=0.95, suggestion="复习平衡移动", practice=""):
    detail = {"recommended_practice": practice} if practice else {}
    return DiagnosisResult(
        barrier_type=barrier, reasoning="reasoning", confidence=conf,
        suggestion=suggestion, detail=detail,
    )


def test_double_high_consistent_fusion():
    r = fuse(_rule(conf=0.9), _llm(barrier="concept", conf=0.95))
    assert r.barrier_type == "concept"
    assert r.fused_conf == pytest.approx(0.6 * 0.9 + 0.4 * 0.95)
    assert r.has_conflict is False
    assert r.label == "high"
    assert r.diagnosis_flag == "normal"
    assert r.source == "fused"


def test_rule_high_conflict_rule_wins():
    r = fuse(_rule(conf=0.9), _llm(barrier="reading", conf=0.5))
    assert r.has_conflict is True
    assert r.barrier_type == "concept"  # 规则高置信 → 以规则为准
    assert r.diagnosis_flag == "normal"
    assert r.label == "high"


def test_llm_high_conflict_llm_wins():
    r = fuse(_rule(conf=0.6), _llm(barrier="reading", conf=0.95))
    assert r.has_conflict is True
    assert r.barrier_type == "reading"  # LLM 高置信 → 以 LLM 为准
    assert r.diagnosis_flag == "normal"
    assert r.label == "high"


def test_double_low_conflict_manual_review():
    r = fuse(_rule(conf=0.4), _llm(barrier="reading", conf=0.5))
    assert r.has_conflict is True
    assert r.diagnosis_flag == "manual_review"  # 双低 → 人工审核
    assert r.barrier_type == "reading"  # 取高侧（llm 0.5 > rule 0.4）


def test_single_side_scaling_and_label():
    r_only = fuse(_rule(conf=0.9), None)
    assert r_only.fused_conf == pytest.approx(0.6 * 0.9)  # 0.54
    assert r_only.source == "rule"
    assert r_only.label == "high"  # 单侧按源置信度打标（T5）

    l_only = fuse(None, _llm(conf=0.7))
    assert l_only.fused_conf == pytest.approx(0.4 * 0.7)  # 0.28
    assert l_only.source == "llm"
    assert l_only.label == "medium"


def test_label_boundaries():
    assert _label(0.0) == "low"
    assert _label(0.599) == "low"
    assert _label(0.6) == "medium"
    assert _label(0.799) == "medium"
    assert _label(0.8) == "high"
    assert _label(1.0) == "high"


def test_remediation_dedup():
    rule = _rule(rems=("复习勒夏特列", "回顾升温方向"))
    llm = _llm(suggestion="回顾升温方向", practice="练习3道平衡题")
    r = fuse(rule, llm)
    assert r.remediation == ["复习勒夏特列", "回顾升温方向", "练习3道平衡题"]
    assert len(r.remediation) == len(set(r.remediation))


def test_double_high_conflict_flag_manual_review():
    r = fuse(_rule(conf=0.9), _llm(barrier="reading", conf=0.95))
    assert r.has_conflict is True
    assert r.diagnosis_flag == "manual_review"  # 双高冲突 → manual_review
    assert r.barrier_type == "reading"  # 取高侧


def test_llm_error_treated_as_absent():
    err = error_signal("LLM 调用失败")
    r = fuse(_rule(conf=0.9), err)
    assert r.source == "rule"
    assert r.fused_conf == pytest.approx(0.6 * 0.9)
    assert r.diagnosis_flag == "normal"


def test_both_absent_error_flag():
    r = fuse(None, None)
    assert r.barrier_type is None
    assert r.diagnosis_flag == "error"
    assert r.source == "error"
    assert r.fused_conf == 0.0


def test_weights_normalized_and_used():
    rw, lw = _normalize_weights(3, 2)
    assert rw == pytest.approx(0.6) and lw == pytest.approx(0.4)
    assert RULE_WEIGHT == 0.6 and LLM_WEIGHT == 0.4


def test_question_id_passthrough():
    r = fuse(_rule(conf=0.9), _llm(conf=0.95), question_id=42)
    assert r.question_id == 42


def test_zero_weights_fallback_to_defaults():
    # 9.1 覆盖率补齐：权重和为 0 → 回退默认 0.6/0.4
    rw, lw = _normalize_weights(0, 0)
    assert rw == pytest.approx(RULE_WEIGHT) and lw == pytest.approx(LLM_WEIGHT)

    r = fuse(_rule(conf=0.9), _llm(conf=0.95), rw=0, lw=0)
    assert r.fused_conf == pytest.approx(0.6 * 0.9 + 0.4 * 0.95)
    assert r.source == "fused"


def test_clamp01_non_numeric_returns_zero():
    # code review 加固：与 llm_diagnosis._clamp01 对齐，非数值回退 0.0
    assert _clamp01("abc") == 0.0
    assert _clamp01(None) == 0.0
    assert _clamp01(0.5) == 0.5
    assert _clamp01(1.5) == 1.0
