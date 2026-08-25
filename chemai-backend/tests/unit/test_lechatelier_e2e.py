"""勒夏特列方向混淆端到端（A 级，doc 48 §7）。

串联规则引擎 + LLM mock（判 concept）+ 融合引擎，验证：
barrier_type=concept、has_conflict=false、置信度标签正确、补救建议去重、
fused_conf 符合 0.6×rule + 0.4×llm。
"""
import pytest

from app.services.diagnosis.fusion import fuse
from app.services.diagnosis.llm_diagnosis import diagnose_llm
from app.services.diagnosis.rule_engine import ChemistryRuleEngine

QUESTION = "在恒容密闭容器中，反应 A(g)+B(g)⇌C(g) 已达平衡，升高温度后平衡如何移动？"
WRONG_ANSWER = "升高温度，平衡向放热反应方向移动"

LLM_CONCEPT_JSON = (
    '{"barrier_type": "concept", "reasoning": "学生混淆了升温时的平衡移动方向，'
    '应朝吸热方向移动。", "confidence": 0.9, "suggestion": "回顾勒夏特列原理：升温向吸热方向移动。"}'
)


class MockLLM:
    def __init__(self, response):
        self._response = response
        self.calls = 0

    def complete(self, messages):
        self.calls += 1
        return self._response


def test_lechatelier_e2e_full_chain():
    engine = ChemistryRuleEngine()

    # 1. 规则引擎
    top = engine.top_diagnosis(WRONG_ANSWER, QUESTION)
    assert top is not None
    assert top.rule_id == "RULE_001"
    assert top.barrier_type == "concept"
    assert top.confidence > 0.7

    # 2. LLM 深度诊断（mock 判 concept）
    llm = diagnose_llm(
        {"question": QUESTION, "answer": WRONG_ANSWER, "correct_answer": "升温向吸热方向移动"},
        client=MockLLM(LLM_CONCEPT_JSON),
    )
    assert not llm.is_error
    assert llm.barrier_type == "concept"

    # 3. 融合引擎
    result = fuse(top, llm, question_id=101)
    assert result.barrier_type == "concept"
    assert result.has_conflict is False
    assert result.fused_conf == pytest.approx(0.6 * top.confidence + 0.4 * 0.9)
    assert result.label == "high"  # 两侧均高置信
    assert result.diagnosis_flag == "normal"
    # 补救建议去重（rule 与 llm 补救不重复）
    assert len(result.remediation) == len(set(result.remediation))
    assert result.question_id == 101

    # 输出结构：top_diagnosis / fused_conf / 标签 / 补救建议
    payload = result.to_dict()
    assert payload["barrier_type"] == "concept"
    assert payload["label"] == "high"
    assert payload["remediation"]
