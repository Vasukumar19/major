"""Unit tests for Unified Evidence Engine."""
from __future__ import annotations

from patchforge.diagnosis.unified_evidence import UnifiedEvidenceEngine, UnifiedEvidenceItem


def test_unified_evidence_engine():
    engine = UnifiedEvidenceEngine()
    e1 = engine.add_evidence(
        source="TRACEBACK",
        target="requests/sessions.py:Session.merge_environment_settings",
        claim="Traceback passes through merge_environment_settings",
        confidence=1.0,
        supports=["H1"],
    )
    e2 = engine.add_evidence(
        source="CODE_GRAPH",
        target="requests/utils.py:get_environ_proxies",
        claim="get_environ_proxies is shared infrastructure with 40 callers",
        confidence=0.9,
        contradicts=["H2"],
    )

    assert len(engine.items) == 2
    assert e1.id == "E1"
    assert e2.id == "E2"

    score_h1, sup_h1, con_h1 = engine.evaluate_hypothesis_support("H1")
    assert score_h1 == 1.0
    assert len(sup_h1) == 1
    assert len(con_h1) == 0

    score_h2, sup_h2, con_h2 = engine.evaluate_hypothesis_support("H2")
    assert score_h2 < 0.0  # penalized by contradiction
    assert len(con_h2) == 1

    summary = engine.format_summary_for_prompt()
    assert "STRUCTURED UNIFIED EVIDENCE SUBSTRATE" in summary
    assert "E1" in summary
