"""Evals 运行器（L1/L2/L3 金字塔 + 基线对比，task 6.2）。

用法：
    python -m app.evals.runners.run_evals --tier l1
    python -m app.evals.runners.run_evals --tier l3
    python -m app.evals.runners.run_evals --tier all --compare baseline.json

- l1/l2：双层合成正确率（纯函数，固定分数输入，≥90%）。
- l3：题目级评审科学准确性（Golden 子集，专家标注科学分 ±15 容差 ≥75%；
      未配置 llm_api_key 时 SKIP 不阻断）。
- --compare baseline.json：当前通过率比基线劣化 >5% 时退出码非 0。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from app.config import settings
from app.evals.cases import composite_correctness
from app.services.audit.review import (
    FallbackReviewLLMClient,
    ReviewLLMError,
    ReviewLLMClient,
    review_question,
)

L1L2_PASS_RATE = 0.90
L3_PASS_RATE = 0.75
L3_SCORE_TOLERANCE = 15.0
DEGRADATION_THRESHOLD = 0.05

GOLDEN_PATH = Path(__file__).resolve().parent.parent / "golden" / "review_scientificity.jsonl"


def _load_golden(path: Path) -> list[dict]:
    cases: list[dict] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    return cases


def _review_scientificity_accuracy(client: ReviewLLMClient) -> float | None:
    """题目级评审科学准确性：|预测科学分 - 专家标注| ≤ ±15 视为准确。

    未配置 llm_api_key 时返回 None（SKIP）。
    """
    if not settings.llm_api_key:
        return None
    golden = _load_golden(GOLDEN_PATH)
    if not golden:
        return None
    matches = 0
    for case in golden:
        try:
            result = review_question(case, client)
        except ReviewLLMError:
            continue
        if abs(result.scores.scientificity - case["scientificity"]) <= L3_SCORE_TOLERANCE:
            matches += 1
    return matches / len(golden)


def _degradation_guard(results: dict[str, float], baseline_path: str | None) -> None:
    if not baseline_path or not os.path.exists(baseline_path):
        return
    with open(baseline_path, encoding="utf-8") as f:
        baseline = json.load(f)
    for key, rate in results.items():
        base = baseline.get(key, 1.0)
        if base - rate > DEGRADATION_THRESHOLD:
            raise SystemExit(
                f"EVAL_DEGRADED: {key} 基线 {base:.0%} → 当前 {rate:.0%}，劣化超 {DEGRADATION_THRESHOLD:.0%}"
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ChemAI Evals 运行器")
    parser.add_argument("--tier", choices=["l1", "l2", "l3", "all"], default="all")
    parser.add_argument("--compare", default=None, help="基线 JSON 路径")
    args = parser.parse_args(argv)

    results: dict[str, float] = {}
    failures: list[str] = []

    if args.tier in ("l1", "l2", "all"):
        rates = composite_correctness()
        results.update(rates)
        for name, rate in rates.items():
            status = "PASS" if rate >= L1L2_PASS_RATE else "FAIL"
            print(f"[l1/l2] {name}: {rate:.0%} {status}")
            if rate < L1L2_PASS_RATE:
                failures.append(f"{name} {rate:.0%} < {L1L2_PASS_RATE:.0%}")

    if args.tier in ("l3", "all"):
        accuracy = _review_scientificity_accuracy(FallbackReviewLLMClient())
        if accuracy is None:
            print("[l3] review_scientificity: SKIP（未配置 llm_api_key）")
        else:
            status = "PASS" if accuracy >= L3_PASS_RATE else "FAIL"
            print(f"[l3] review_scientificity: {accuracy:.0%} {status}")
            results["review_scientificity"] = accuracy
            if accuracy < L3_PASS_RATE:
                failures.append(f"review_scientificity {accuracy:.0%} < {L3_PASS_RATE:.0%}")

    _degradation_guard(results, args.compare)
    if failures:
        for f in failures:
            print(f"FAILED: {f}", file=sys.stderr)
        return 1
    print("ALL EVALS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
