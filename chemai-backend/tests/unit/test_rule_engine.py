"""ChemistryRuleEngine 规则引擎测试（doc 48 §3 + 评审 T9 证据加权）。

覆盖：22 条规则 schema 加载、勒夏特列命中（置信度>0.7）、anti_keywords 排除、
空答案/正确答案不触发、多规则置信度降序、clamp、severity 加成、min 阈值、
搜索文本证据加权（答案为主）。
"""
import re

import pytest

from app.services.diagnosis.rule_engine import (
    CONFIDENCE_BASE,
    MIN_KEYWORD_MATCH,
    MIN_PATTERN_MATCH,
    QUESTION_WEIGHT,
    ChemistryRuleEngine,
)

# 勒夏特列方向混淆错误作答（e2e A 级输入）
LECHATELIER_WRONG = "升高温度，平衡向放热反应方向移动"


def _fabricated_rule(severity=3, modifier=0.0, keywords=None, patterns=None):
    return {
        "id": "RULE_TEST",
        "category": "测试",
        "name": "测试规则",
        "barrier_type": "concept",
        "keywords": keywords or ["k1", "k2"],
        "anti_keywords": [],
        "patterns": patterns or ["k1.*k2"],
        "severity": severity,
        "confidence_modifier": modifier,
        "remediation_points": ["补救"],
        "related_questions": [],
    }


def test_rules_load_22_and_schema():
    rules = ChemistryRuleEngine._load_rules()
    assert len(rules) == 22
    required = {
        "id", "category", "name", "barrier_type", "keywords",
        "anti_keywords", "patterns", "severity", "confidence_modifier",
        "remediation_points", "related_questions",
    }
    for r in rules:
        assert required.issubset(r.keys()), f"{r.get('id')} missing fields"
        assert len(r["keywords"]) >= MIN_KEYWORD_MATCH
        assert len(r["patterns"]) >= MIN_PATTERN_MATCH
        for pat in r["patterns"]:
            re.compile(pat)  # 正则可编译
        assert r["barrier_type"] in {"concept", "expression"}


def test_lechatelier_direction_confusion_high_confidence():
    engine = ChemistryRuleEngine()
    results = engine.analyze(LECHATELIER_WRONG, "根据勒夏特列原理判断平衡移动方向")
    assert results, "勒夏特列错误作答应命中规则"
    top = results[0]
    assert top.rule_id == "RULE_001"
    assert top.barrier_type == "concept"
    assert top.confidence > 0.7


def test_anti_keywords_exclude():
    engine = ChemistryRuleEngine()
    results = engine.analyze("催化剂不改变平衡移动方向", "")
    assert all(m.rule_id != "RULE_003" for m in results)


def test_empty_answer_no_match():
    engine = ChemistryRuleEngine()
    assert engine.analyze("", "") == []


def test_correct_answer_no_trigger():
    engine = ChemistryRuleEngine()
    assert engine.analyze(LECHATELIER_WRONG, "", is_correct=True) == []


def test_multiple_rules_sorted_by_confidence_desc():
    engine = ChemistryRuleEngine()
    answer = "平衡常数与反应速率无关，但升温使反应速率加快，平衡向放热方向移动"
    results = engine.analyze(answer, "")
    assert len(results) >= 2
    confs = [m.confidence for m in results]
    assert confs == sorted(confs, reverse=True)
    assert results[0].rule_id == "RULE_001"


def test_confidence_clamped_to_unit_interval():
    high = _fabricated_rule(severity=5, modifier=0.05,
                            keywords=["k1", "k2"], patterns=[".*"])
    low = _fabricated_rule(severity=1, modifier=-0.9,
                           keywords=["k1", "k2"], patterns=[".*"])
    assert ChemistryRuleEngine._confidence(high, 2.0, 1.0) == pytest.approx(1.0)
    assert ChemistryRuleEngine._confidence(low, 0.0, 0.0) == pytest.approx(0.0)


def test_severity_bonus_raises_confidence():
    base = _fabricated_rule(severity=3, keywords=["k1", "k2"], patterns=[".*"])
    severe = _fabricated_rule(severity=5, keywords=["k1", "k2"], patterns=[".*"])
    low = _fabricated_rule(severity=1, keywords=["k1", "k2"], patterns=[".*"])
    c_base = ChemistryRuleEngine._confidence(base, 1.0, 0.0)
    c_severe = ChemistryRuleEngine._confidence(severe, 1.0, 0.0)
    c_low = ChemistryRuleEngine._confidence(low, 1.0, 0.0)
    assert c_severe > c_base > c_low
    # severity_bonus=(severity-3)*0.05：severity 每差 1 差 0.05
    assert c_severe - c_base == pytest.approx(0.10)
    assert c_base - c_low == pytest.approx(0.10)


def test_min_threshold_no_fire():
    engine = ChemistryRuleEngine()
    # 单关键词 + 无正则命中 → 不触发
    assert engine.analyze("催化剂", "") == []


def test_evidence_weighting_answer_over_question():
    engine = ChemistryRuleEngine()
    # 答案命中证据
    in_answer = engine.analyze(
        "浓度商Q大于K时平衡向逆向移动", "判断平衡移动方向")
    # 仅题目正文命中证据
    in_question = engine.analyze(
        "我这样判断对吗", "当浓度商Q大于K时平衡移动方向如何")
    assert len(in_answer) == 1 and in_answer[0].rule_id == "RULE_004"
    assert len(in_question) == 1 and in_question[0].rule_id == "RULE_004"
    assert in_answer[0].confidence > in_question[0].confidence


def test_confidence_formula_matches_doc():
    # 精度优先刻意偏差（D3）：CONFIDENCE_BASE 与规则优先阈值 0.85 同值属意图
    assert CONFIDENCE_BASE == 0.85
    assert 0.0 < QUESTION_WEIGHT < 1.0


# ---------------- 9.1 覆盖率补齐（_validate_rule 各失败分支） ----------------

def _bad_rule():
    return _fabricated_rule()


def test_validate_missing_field_raises():
    bad = _bad_rule()
    del bad["id"]
    with pytest.raises(ValueError):
        ChemistryRuleEngine._validate_rule(bad)


def test_validate_few_keywords_raises():
    bad = _bad_rule()
    bad["keywords"] = ["k1"]
    with pytest.raises(ValueError):
        ChemistryRuleEngine._validate_rule(bad)


def test_validate_no_pattern_raises():
    bad = _bad_rule()
    bad["patterns"] = []
    with pytest.raises(ValueError):
        ChemistryRuleEngine._validate_rule(bad)


def test_validate_illegal_barrier_type_raises():
    bad = _bad_rule()
    bad["barrier_type"] = "typo"
    with pytest.raises(ValueError):
        ChemistryRuleEngine._validate_rule(bad)


def test_validate_bad_severity_raises():
    bad = _bad_rule()
    bad["severity"] = 6
    with pytest.raises(ValueError):
        ChemistryRuleEngine._validate_rule(bad)
