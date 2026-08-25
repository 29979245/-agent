"""障碍诊断 L3 eval（9.1a）：mock LLM 下规则+融合全链路结构与准确率代理。

Golden 数据（勒夏特列/氧化还原/摩尔/审题/表述，带标注 barrier_type）经规则引擎 +
脚本化 mock LLM + 融合引擎：断言结构完整、barrier_type 枚举合法、置信度标签符合
阈值边界；准确率代理（融合主判 == 标注）≥70%。mock LLM 故无需 llm_api_key。
"""
from app.evals.diagnosis import DIAGNOSIS_PASS_RATE, evaluate_diagnosis_barrier


def test_diagnosis_l3_structure_and_accuracy():
    result = evaluate_diagnosis_barrier()
    assert result["evaluated"] >= 8, f"Golden 数据集过小: {result['evaluated']}"
    assert result["structural"], f"结构断言失败: {result['structural_failures']}"
    assert (
        result["accuracy"] >= DIAGNOSIS_PASS_RATE
    ), f"准确率代理 {result['accuracy']:.0%} < {DIAGNOSIS_PASS_RATE:.0%}"
