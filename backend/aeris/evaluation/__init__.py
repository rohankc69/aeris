"""Evaluation harness: run scenarios through decision providers and compare them.

AERIS does not merely call Jev; it evaluates Jev. The same scenario, seed and mission logic
run under different providers, and their DecisionRecords and metrics are written as JSON.
Recorded decision inputs can also be replayed through a provider offline.
"""

from aeris.evaluation.harness import (
    EvalResult,
    ReplayResult,
    compare_results,
    load_result,
    replay_records,
    run_evaluation,
)

__all__ = [
    "EvalResult",
    "ReplayResult",
    "compare_results",
    "load_result",
    "replay_records",
    "run_evaluation",
]
