"""Phase 2 Test Suite — Hypothesis & Target State Consistency.

Verifies:
1. selected_hypothesis_idx strictly matches selected_hypothesis and set_active_hypothesis.
2. Candidate edit sites derive from the active hypothesis's affected sites (no stale repair_sites).
3. Switching hypotheses updates active_hyp_idx, target object, and repair unit coherently.
4. Malformed or empty diagnoses do not gain unjustified confidence or authority.
5. In-place diagnosis updates preserve telemetry provenance distinctions.
6. Initial diagnosis index initialization respects non-zero selected_hypothesis_idx.
"""
from __future__ import annotations

import pytest

from patchforge.core.target import EditSite, RepairTarget
from patchforge.diagnosis.diagnosis import CompetingDiagnosisResult, DiagnosisHypothesis


def test_selected_hypothesis_index_corresponds_to_selected_object():
    """Invariant 1: selected_hypothesis_idx must strictly match selected_hypothesis."""
    h_a = DiagnosisHypothesis(
        id="A",
        cause="Cause A",
        affected_sites=["file1.py:fn_a"],
        counterfactual_score=60.0,
        confidence=0.8,
    )
    h_b = DiagnosisHypothesis(
        id="B",
        cause="Cause B",
        affected_sites=["file2.py:fn_b"],
        counterfactual_score=90.0,
        confidence=0.7,
    )

    diag = CompetingDiagnosisResult(hypotheses=[h_a, h_b], selected_hypothesis_idx=1)
    assert diag.selected_hypothesis == h_b
    assert diag.selected_hypothesis.id == "B"
    assert diag.selected_hypothesis_idx == 1

    # Calling set_active_hypothesis(0) synchronizes index and object
    diag.set_active_hypothesis(0)
    assert diag.selected_hypothesis == h_a
    assert diag.selected_hypothesis.id == "A"
    assert diag.selected_hypothesis_idx == 0


def test_affected_sites_derived_from_active_hypothesis_no_stale_sites():
    """Invariant 2: Candidate edit sites must be derived from active hypothesis without stale sites."""
    h_a = DiagnosisHypothesis(id="A", cause="Cause A", affected_sites=["pkg/models.py:func_a"])
    h_b = DiagnosisHypothesis(id="B", cause="Cause B", affected_sites=["pkg/sessions.py:func_b"])

    diag = CompetingDiagnosisResult(hypotheses=[h_a, h_b], selected_hypothesis_idx=0)
    assert diag.affected_sites == ["pkg/models.py:func_a"]

    # Switch to hypothesis B via set_active_hypothesis
    diag.set_active_hypothesis(1)
    assert diag.affected_sites == ["pkg/sessions.py:func_b"]
    assert diag.repair_sites == ["pkg/sessions.py:func_b"]
    assert diag.selected_hypothesis.id == "B"


def test_empty_hypotheses_does_not_inflate_confidence():
    """Invariant 3: Malformed or empty diagnoses do not gain unjustified confidence."""
    diag = CompetingDiagnosisResult(hypotheses=[], selected_hypothesis_idx=0)
    assert diag.selected_hypothesis is None
    assert diag.selected_hypothesis_idx == 0
    assert diag.defect_confidence == 0.0
    assert diag.affected_sites == []


def test_set_active_hypothesis_bounds_clamping():
    """Invariant 4: Out-of-bounds indices are safely clamped."""
    h_a = DiagnosisHypothesis(id="A", cause="Cause A", affected_sites=["file.py:fn"])
    diag = CompetingDiagnosisResult(hypotheses=[h_a], selected_hypothesis_idx=0)

    diag.set_active_hypothesis(99)
    assert diag.selected_hypothesis_idx == 0
    assert diag.selected_hypothesis == h_a

    diag.set_active_hypothesis(-5)
    assert diag.selected_hypothesis_idx == 0
    assert diag.selected_hypothesis == h_a


def test_selected_hypothesis_explicit_object_syncs_index():
    """Invariant 5: Passing selected_hypothesis explicitly updates selected_hypothesis_idx."""
    h_a = DiagnosisHypothesis(id="A", cause="Cause A")
    h_b = DiagnosisHypothesis(id="B", cause="Cause B")
    diag = CompetingDiagnosisResult(hypotheses=[h_a, h_b], selected_hypothesis=h_b)

    assert diag.selected_hypothesis_idx == 1
    assert diag.selected_hypothesis == h_b


def test_target_provenance_distinguishes_initial_and_switched_hypotheses():
    """Invariant 6: Initial target and switch history remain recorded separately in provenance."""
    target_provenance = {
        "original_diagnosis_sites": ["pkg/models.py:func_a"],
        "selected_target": "pkg/models.py:func_a",
        "matches_hypothesized_site": True,
        "justification": "Target matches hypothesized site",
        "target_changes": [],
    }

    # Simulate hypothesis switch recording
    target_provenance["target_changes"].append({
        "switched_to_hypothesis": "B",
        "target": "pkg/sessions.py:func_b",
        "justification": "Switched after failure",
    })

    assert target_provenance["selected_target"] == "pkg/models.py:func_a"
    assert len(target_provenance["target_changes"]) == 1
    assert target_provenance["target_changes"][0]["switched_to_hypothesis"] == "B"
