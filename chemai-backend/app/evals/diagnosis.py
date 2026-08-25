"""障碍诊断 L3 eval（9.1a）：mock LLM 下规则引擎 + 融合引擎全链路。

Golden 数据（勒夏特列/氧化还原/摩尔/审题/表述样例，带标注 barrier_type 与脚本化
llm_barrier_type 模拟 LLM 侧非完美召回）经 规则引擎 → mock LLM → 融合引擎。
- 结构断言：输出字段齐全、barrier_type 枚举合法、置信度标签符合阈值边界；
- 准确率代理：融合主判 == 标注 barrier_type 的比例 ≥70%。
mock LLM 注入故无需 llm_api_key，不阻塞 CI。
"""
import json
from pathlib import Path

from app.evals.io import load_jsonl
from app.services.diagnosis.fusion import _label, fuse
from app.services.diagnosis.llm_diagnosis import VALID_BARRIER_TYPES, diagnose_llm
from app.services.diagnosis.rule_engine import ChemistryRuleEngine

DIAGNOSIS_PASS_RATE = 0.70
DIAGNOSIS_GOLDEN_PATH = Path(__file__).resolve().parent / "golden" / "diagnosis_barrier.jsonl"


class ScriptedDiagnosisLLM:
    """脚本化 mock LLM：按 case 的 llm_barrier_type 输出合法 JSON（模拟 LLM 召回非完美）。"""

    def __init__(self, barrier_type: str, confidence: float = 0.9):
        self._bt = barrier_type
        self._conf = confidence

    def complete(self, messages: list[dict]) -> str:
        return json.dumps(
            {
                "barrier_type": self._bt,
                "reasoning": "mock 诊断：模拟 LLM 判读。",
                "confidence": self._conf,
                "suggestion": "mock 补救建议：回归概念辨析。",
            },
            ensure_ascii=False,
        )


def evaluate_diagnosis_barrier(golden_path: Path | None = None) -> dict:
    """运行 Golden 全链路，返回 {accuracy, evaluated, structural, structural_failures}。"""
    engine = ChemistryRuleEngine()
    cases = load_jsonl(golden_path or DIAGNOSIS_GOLDEN_PATH)
    matches = 0
    structural_failures: list[str] = []
    for case in cases:
        client = ScriptedDiagnosisLLM(case["llm_barrier_type"], case.get("llm_confidence", 0.9))
        rule = engine.top_diagnosis(case["answer"], case["question"])
        llm = diagnose_llm(
            {
                "question": case["question"],
                "answer": case["answer"],
                "correct_answer": case["correct_answer"],
            },
            client,
        )
        fused = fuse(rule, llm, question_id=case.get("id"))
        src_conf = max(
            (rule.confidence if rule else 0.0),
            (llm.confidence if llm and not llm.is_error else 0.0),
        )
        ok = (
            fused.barrier_type in VALID_BARRIER_TYPES
            and fused.label == _label(src_conf)
            and 0.0 <= fused.fused_conf <= 1.0
        )
        if not ok:
            structural_failures.append(case["id"])
        if fused.barrier_type == case["barrier_type"]:
            matches += 1
    return {
        "accuracy": matches / len(cases) if cases else 0.0,
        "evaluated": len(cases),
        "structural": not structural_failures,
        "structural_failures": structural_failures,
    }
