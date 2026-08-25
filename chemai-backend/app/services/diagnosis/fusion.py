"""置信度融合引擎（doc 48 §5）。

双引擎混合诊断的融合侧：规则（权重 0.6，精度优先）+ LLM（权重 0.4，召回补充）。
fused = clamp(0.6×rule_conf + 0.4×llm_conf)（权重非归一化时自动归一化）。
冲突检测 + 决策表消解：rule≥0.85 规则胜 / llm≥0.9 LLM 胜 / 双低"需人工审核"取高侧 /
双高冲突（两侧置信度均高且类别不一致）→ diagnosis_flag=manual_review。
单侧按源置信度折算 fused 并打标（T5：标签与 fused_conf 解耦）。
"""
from dataclasses import dataclass, field

from app.services.diagnosis.llm_diagnosis import DiagnosisResult
from app.services.diagnosis.rule_engine import RuleMatch

# 融合权重（非归一化时自动归一化）
RULE_WEIGHT = 0.6
LLM_WEIGHT = 0.4
# 决策表阈值（D3：RULE_WINS_THRESHOLD 与 CONFIDENCE_BASE=0.85 同值属刻意精度优先）
RULE_WINS_THRESHOLD = 0.85
LLM_WINS_THRESHOLD = 0.9
# 置信度标签边界：<0.6 low / 0.6-0.8 medium / ≥0.8 high
LABEL_LOW = 0.6
LABEL_MEDIUM = 0.8

FLAG_NORMAL = "normal"
FLAG_MANUAL = "manual_review"
FLAG_NEEDS_ATTENTION = "needs_attention"
FLAG_ERROR = "error"


@dataclass
class FusionResult:
    """融合输出：barrier_type 主判 + fused_conf 落库 + 标签 + 诊断标志 + 补救。"""

    barrier_type: str | None
    fused_conf: float
    rule_conf: float | None
    llm_conf: float | None
    has_conflict: bool
    label: str  # low / medium / high
    diagnosis_flag: str  # normal / manual_review / needs_attention / error
    source: str  # fused / rule / llm / error
    remediation: list[str] = field(default_factory=list)
    reasoning: str = ""
    suggestion: str = ""
    detail: dict = field(default_factory=dict)
    question_id: int | None = None

    def to_dict(self) -> dict:
        return {
            "barrier_type": self.barrier_type,
            "fused_conf": round(self.fused_conf, 4),
            "rule_conf": self.rule_conf,
            "llm_conf": self.llm_conf,
            "has_conflict": self.has_conflict,
            "label": self.label,
            "diagnosis_flag": self.diagnosis_flag,
            "source": self.source,
            "remediation": self.remediation,
            "reasoning": self.reasoning,
            "suggestion": self.suggestion,
            "question_id": self.question_id,
        }


def _clamp01(value: float) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _normalize_weights(rw: float, lw: float) -> tuple[float, float]:
    total = rw + lw
    if total <= 0:
        return RULE_WEIGHT, LLM_WEIGHT
    return rw / total, lw / total


def _label(conf: float) -> str:
    if conf >= LABEL_MEDIUM:
        return "high"
    if conf >= LABEL_LOW:
        return "medium"
    return "low"


def _dedup(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        item = item.strip()
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _llm_remediation(llm: DiagnosisResult) -> list[str]:
    items = [llm.suggestion]
    practice = llm.detail.get("recommended_practice") if llm.detail else None
    if practice:
        items.append(str(practice))
    return _dedup(items)


def fuse(
    rule: RuleMatch | None,
    llm: DiagnosisResult | None,
    question_id: int | None = None,
    rw: float = RULE_WEIGHT,
    lw: float = LLM_WEIGHT,
) -> FusionResult:
    """融合规则与 LLM 诊断。LLM 错误信号视为单侧规则；两侧缺失 → error。"""
    if llm is not None and llm.is_error:
        llm = None

    rule_conf = rule.confidence if rule else None
    llm_conf = llm.confidence if llm else None
    nrw, nlw = _normalize_weights(rw, lw)

    if rule is None and llm is None:
        return FusionResult(
            barrier_type=None, fused_conf=0.0, rule_conf=None, llm_conf=None,
            has_conflict=False, label="low", diagnosis_flag=FLAG_ERROR,
            source="error", question_id=question_id,
        )

    if rule is not None and llm is None:
        fused = _clamp01(rule_conf * nrw)
        return FusionResult(
            barrier_type=rule.barrier_type, fused_conf=fused,
            rule_conf=rule_conf, llm_conf=None, has_conflict=False,
            label=_label(rule_conf), diagnosis_flag=FLAG_NORMAL, source="rule",
            remediation=list(rule.remediation_points), reasoning=rule.name,
            question_id=question_id,
        )

    if rule is None and llm is not None:
        fused = _clamp01(llm_conf * nlw)
        return FusionResult(
            barrier_type=llm.barrier_type, fused_conf=fused,
            rule_conf=None, llm_conf=llm_conf, has_conflict=False,
            label=_label(llm_conf), diagnosis_flag=FLAG_NORMAL, source="llm",
            remediation=_llm_remediation(llm), reasoning=llm.reasoning,
            suggestion=llm.suggestion, question_id=question_id,
        )

    # 双侧齐全：冲突检测 + 决策表
    has_conflict = rule.barrier_type != llm.barrier_type
    fused = _clamp01(rule_conf * nrw + llm_conf * nlw)
    both_high = rule_conf >= RULE_WINS_THRESHOLD and llm_conf >= LLM_WINS_THRESHOLD

    if not has_conflict:
        barrier, label_src, flag, source = rule.barrier_type, max(rule_conf, llm_conf), FLAG_NORMAL, "fused"
    elif both_high:
        barrier, label_src, flag = _high_side(rule, llm), max(rule_conf, llm_conf), FLAG_MANUAL
        source = "fused"
    elif rule_conf >= RULE_WINS_THRESHOLD:
        barrier, label_src, flag, source = rule.barrier_type, rule_conf, FLAG_NORMAL, "fused"
    elif llm_conf >= LLM_WINS_THRESHOLD:
        barrier, label_src, flag, source = llm.barrier_type, llm_conf, FLAG_NORMAL, "fused"
    else:
        barrier, label_src, flag = _high_side(rule, llm), max(rule_conf, llm_conf), FLAG_MANUAL
        source = "fused"

    return FusionResult(
        barrier_type=barrier, fused_conf=fused,
        rule_conf=rule_conf, llm_conf=llm_conf, has_conflict=has_conflict,
        label=_label(label_src), diagnosis_flag=flag, source=source,
        remediation=_dedup(list(rule.remediation_points) + _llm_remediation(llm)),
        reasoning=rule.name, suggestion=llm.suggestion,
        detail=llm.detail, question_id=question_id,
    )


def _high_side(rule: RuleMatch, llm: DiagnosisResult) -> str:
    return rule.barrier_type if rule.confidence >= llm.confidence else llm.barrier_type
