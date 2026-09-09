"""Deterministic, transparent scoring. No learned weights (Phase 1).

score = semantic_evidence + structural_evidence + test_evidence
        + error_evidence + consistency
Each term is documented at the call site; weights are fixed constants.
"""
from __future__ import annotations

W_AGENT_FILE = 1.0
W_AGENT_RELATED = 0.3
W_AGENT_EDIT_LOC = 0.2
W_GRAPH_NEIGHBOR = 0.2
W_ISSUE_MENTION = 0.1
W_TEST_MENTION = 0.15


def rank_key(score: float, order: int) -> tuple:
    return (-score, order)


def normalize(scores: list[float]) -> list[float]:
    if not scores:
        return []
    top = max(scores)
    if top <= 0:
        return [0.0 for _ in scores]
    return [s / top for s in scores]
