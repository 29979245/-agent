"""化学障碍规则引擎（doc 48 §3）。

双引擎混合诊断的规则侧（权重 0.6）：从 rules.yaml 惰性加载 22 条规则，
以学生答案为主、题目正文为辅的搜索文本做关键词/正则/反关键词匹配，
输出按置信度降序的 RuleMatch 列表。阅读（reading）障碍无规则覆盖，走 LLM 路径。

精度优先刻意偏差（D3）：CONFIDENCE_BASE=0.85 与融合决策表"规则优先阈值 0.85"
同值属意图非缺陷 —— 高置信规则命中即视为可靠证据。
"""
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

# 置信度公式：conf = clamp(0.85 + severity_bonus + kw_rate*0.04 + pat_rate*0.06 + modifier, 0, 1)
CONFIDENCE_BASE = 0.85
KEYWORD_WEIGHT = 0.04
PATTERN_WEIGHT = 0.06
SEVERITY_FACTOR = 0.05  # severity_bonus=(severity-3)*0.05
MIN_KEYWORD_MATCH = 2
MIN_PATTERN_MATCH = 1
# 搜索文本证据加权（T9/评审 C13）：题目正文命中按 0.5 折算，答案命中按 1.0
QUESTION_WEIGHT = 0.5

_RULES_PATH = Path(__file__).parent / "rules" / "rules.yaml"

_REQUIRED_FIELDS = {
    "id", "category", "name", "barrier_type", "keywords",
    "anti_keywords", "patterns", "severity", "confidence_modifier",
    "remediation_points", "related_questions",
}


@dataclass(frozen=True)
class RuleMatch:
    """单条规则命中结果（融合引擎以 rule_conf=confidence 消费）。"""

    rule_id: str
    category: str
    name: str
    barrier_type: str  # concept / expression
    confidence: float
    severity: int
    remediation_points: tuple[str, ...]
    related_questions: tuple[str, ...]


class ChemistryRuleEngine:
    """规则引擎：lazy 加载 + schema 校验 + 混合证据匹配。"""

    _cache: list[dict] | None = None

    @classmethod
    def _load_rules(cls) -> list[dict]:
        if cls._cache is None:
            data = yaml.safe_load(_RULES_PATH.read_text(encoding="utf-8"))
            rules = data["rules"]
            for rule in rules:
                cls._validate_rule(rule)
            cls._cache = rules
        return cls._cache

    @staticmethod
    def _validate_rule(rule: dict) -> None:
        missing = _REQUIRED_FIELDS - rule.keys()
        if missing:
            raise ValueError(f"规则 {rule.get('id')} 缺字段: {missing}")
        if len(rule["keywords"]) < MIN_KEYWORD_MATCH:
            raise ValueError(f"规则 {rule['id']} 关键词不足 {MIN_KEYWORD_MATCH} 个")
        if len(rule["patterns"]) < MIN_PATTERN_MATCH:
            raise ValueError(f"规则 {rule['id']} 正则不足 {MIN_PATTERN_MATCH} 条")
        for pat in rule["patterns"]:
            re.compile(pat)  # 正则可编译
        if rule["barrier_type"] not in {"concept", "expression"}:
            raise ValueError(f"规则 {rule['id']} barrier_type 非法: {rule['barrier_type']}")
        if not 1 <= rule["severity"] <= 5:
            raise ValueError(f"规则 {rule['id']} severity 需在 1-5")

    @staticmethod
    def _confidence(rule: dict, kw_effective: float, pat_effective: float) -> float:
        kw_rate = kw_effective / max(len(rule["keywords"]), 1)
        pat_rate = pat_effective / max(len(rule["patterns"]), 1)
        severity_bonus = (rule["severity"] - 3) * SEVERITY_FACTOR
        raw = (
            CONFIDENCE_BASE
            + severity_bonus
            + kw_rate * KEYWORD_WEIGHT
            + pat_rate * PATTERN_WEIGHT
            + rule["confidence_modifier"]
        )
        return max(0.0, min(1.0, raw))

    def analyze(
        self, answer_text: str, question_text: str = "", is_correct: bool = False
    ) -> list[RuleMatch]:
        """对作答文本诊断。正确答案或空答案不产生任何诊断。"""
        answer = (answer_text or "").strip()
        if is_correct or not answer:
            return []
        question = question_text or ""
        matches = [
            m for r in self._load_rules() if (m := self._match_rule(r, answer, question)) is not None
        ]
        matches.sort(key=lambda m: m.confidence, reverse=True)
        return matches

    def _match_rule(self, rule: dict, answer: str, question: str) -> RuleMatch | None:
        combined = f"{answer}\n{question}"
        for ak in rule["anti_keywords"]:
            if ak and ak in combined:
                return None

        kw_in_answer = [k for k in rule["keywords"] if k in answer]
        kw_in_question_only = [k for k in rule["keywords"] if k not in answer and k in question]
        if len(kw_in_answer) + len(kw_in_question_only) < MIN_KEYWORD_MATCH:
            return None

        pat_in_answer = [p for p in rule["patterns"] if re.search(p, answer)]
        pat_in_question_only = [
            p for p in rule["patterns"]
            if p not in pat_in_answer and re.search(p, question)
        ]
        if len(pat_in_answer) + len(pat_in_question_only) < MIN_PATTERN_MATCH:
            return None

        kw_effective = len(kw_in_answer) + QUESTION_WEIGHT * len(kw_in_question_only)
        pat_effective = len(pat_in_answer) + QUESTION_WEIGHT * len(pat_in_question_only)
        return RuleMatch(
            rule_id=rule["id"],
            category=rule["category"],
            name=rule["name"],
            barrier_type=rule["barrier_type"],
            confidence=self._confidence(rule, kw_effective, pat_effective),
            severity=rule["severity"],
            remediation_points=tuple(rule["remediation_points"]),
            related_questions=tuple(rule.get("related_questions", [])),
        )

    def top_diagnosis(self, answer_text: str, question_text: str = "", is_correct: bool = False) -> RuleMatch | None:
        """返回最高置信度命中（供融合引擎取规则侧）。"""
        results = self.analyze(answer_text, question_text, is_correct)
        return results[0] if results else None
