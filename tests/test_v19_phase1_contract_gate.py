"""Phase 1 Test Suite — Hypothesis Contract Gate Selection Policy.

Verifies:
1. psf__requests-2674 selects Hypothesis B (Response.iter_lines) over lower-score Hypothesis A.
2. psf__requests-1963 selects Hypothesis B (Session.prepare_request) over lower-score Hypothesis A.
3. pallets__flask-4992 maintains selection of Hypothesis A (config.py:from_file).
4. Eliminated upstream hypothesis cannot win; best eligible survivor wins.
5. Missing/None/NaN/inf scores handled safely and deterministically without crashes.
"""
from __future__ import annotations

import math
import pytest

from patchforge.diagnosis.contract import APIContract, APIParameter
from patchforge.diagnosis.contract_gate import HypothesisContractGate
from patchforge.diagnosis.diagnosis import CompetingDiagnosisResult, DiagnosisHypothesis


def test_requests_2674_selection_restores_iter_lines():
    """Verify requests-2674 selects counterfactual winner Hypothesis B (iter_lines)."""
    h_a = DiagnosisHypothesis(
        id="A",
        cause="PreparedRequest does not handle streaming content",
        repair_strategy="Modify PreparedRequest",
        affected_sites=["requests/models.py:PreparedRequest"],
        confidence=0.80,
        counterfactual_score=75.0,
        eliminated=False,
    )
    h_b = DiagnosisHypothesis(
        id="B",
        cause="Response.iter_lines strips empty lines incorrectly",
        repair_strategy="Update iter_lines and iter_content to preserve empty lines",
        affected_sites=["requests/models.py:iter_lines", "requests/models.py:iter_content"],
        confidence=0.75,
        counterfactual_score=90.0,
        eliminated=False,
    )
    diag = CompetingDiagnosisResult(
        hypotheses=[h_a, h_b],
        selected_hypothesis_idx=1,
        selected_hypothesis=h_b,
        repair_sites=h_b.affected_sites,
        defect_confidence=0.75,
    )
    contract = APIContract(target_symbol="iter_lines")

    res = HypothesisContractGate.evaluate_and_filter(diag, contract)

    assert res.selected_hypothesis is not None
    assert res.selected_hypothesis.id == "B"
    assert res.selected_hypothesis_idx == 1
    assert "requests/models.py:iter_lines" in res.repair_sites
    assert res.selected_hypothesis.affected_sites == res.repair_sites


def test_requests_1963_selection_restores_prepare_request():
    """Verify requests-1963 selects counterfactual winner Hypothesis B (prepare_request)."""
    h_a = DiagnosisHypothesis(
        id="A",
        cause="Session.request does not handle cookies correctly",
        repair_strategy="Modify Session.request",
        affected_sites=["requests/sessions.py:request"],
        confidence=0.80,
        counterfactual_score=50.0,
        eliminated=False,
    )
    h_b = DiagnosisHypothesis(
        id="B",
        cause="Session.prepare_request drops cookies on redirect",
        repair_strategy="Update prepare_request to merge session cookies",
        affected_sites=["requests/sessions.py:prepare_request"],
        confidence=0.75,
        counterfactual_score=70.0,
        eliminated=False,
    )
    diag = CompetingDiagnosisResult(
        hypotheses=[h_a, h_b],
        selected_hypothesis_idx=1,
        selected_hypothesis=h_b,
        repair_sites=h_b.affected_sites,
        defect_confidence=0.75,
    )
    contract = APIContract(target_symbol="prepare_request")

    res = HypothesisContractGate.evaluate_and_filter(diag, contract)

    assert res.selected_hypothesis is not None
    assert res.selected_hypothesis.id == "B"
    assert res.selected_hypothesis_idx == 1
    assert "requests/sessions.py:prepare_request" in res.repair_sites


def test_flask_4992_selection_maintains_golden_target():
    """Verify flask-4992 retains selection of Hypothesis A (from_file)."""
    h_a = DiagnosisHypothesis(
        id="A",
        cause="Config.from_file does not accept text parameter",
        repair_strategy="Extend from_file signature to accept text parameter",
        affected_sites=["src/flask/config.py:from_file"],
        confidence=0.85,
        counterfactual_score=90.0,
        eliminated=False,
    )
    diag = CompetingDiagnosisResult(
        hypotheses=[h_a],
        selected_hypothesis_idx=0,
        selected_hypothesis=h_a,
        repair_sites=h_a.affected_sites,
        defect_confidence=0.85,
    )
    contract = APIContract(
        target_symbol="from_file",
        parameters=[APIParameter(name="text", origin="ISSUE_EXPLICIT")],
    )

    res = HypothesisContractGate.evaluate_and_filter(diag, contract)

    assert res.selected_hypothesis is not None
    assert res.selected_hypothesis.id == "A"
    assert res.selected_hypothesis_idx == 0
    assert "src/flask/config.py:from_file" in res.repair_sites


def test_eliminated_upstream_hypothesis_cannot_win():
    """Verify that if the upstream selected hypothesis violates contract, it cannot win."""
    h_a = DiagnosisHypothesis(
        id="A",
        cause="Proposes forbidden parameter",
        repair_strategy="Add forbidden_param to signature",
        affected_sites=["module.py:func"],
        confidence=0.99,
        counterfactual_score=100.0,
        eliminated=False,
    )
    h_b = DiagnosisHypothesis(
        id="B",
        cause="Valid conformant fix",
        repair_strategy="Update internal logic without signature change",
        affected_sites=["module.py:helper"],
        confidence=0.70,
        counterfactual_score=60.0,
        eliminated=False,
    )
    diag = CompetingDiagnosisResult(
        hypotheses=[h_a, h_b],
        selected_hypothesis_idx=0,
        selected_hypothesis=h_a,
        repair_sites=h_a.affected_sites,
        defect_confidence=0.99,
    )
    contract = APIContract(
        target_symbol="func",
        forbidden_parameters=["forbidden_param"],
    )

    res = HypothesisContractGate.evaluate_and_filter(diag, contract)

    assert h_a.eliminated is True
    assert res.selected_hypothesis.id == "B"
    assert res.selected_hypothesis_idx == 1
    assert res.repair_sites == ["module.py:helper"]


def test_non_finite_and_missing_scores_handled_safely():
    """Verify NaN, inf, None, and missing scores do not raise exceptions."""
    h_nan = DiagnosisHypothesis(
        id="A",
        cause="Hypothesis with NaN score",
        repair_strategy="Do something",
        affected_sites=["module.py:a"],
        confidence=float("nan"),
        counterfactual_score=float("nan"),
        eliminated=False,
    )
    h_inf = DiagnosisHypothesis(
        id="B",
        cause="Hypothesis with inf score",
        repair_strategy="Do something else",
        affected_sites=["module.py:b"],
        confidence=float("inf"),
        counterfactual_score=float("inf"),
        eliminated=False,
    )
    h_valid = DiagnosisHypothesis(
        id="C",
        cause="Hypothesis with valid score",
        repair_strategy="Valid fix",
        affected_sites=["module.py:c"],
        confidence=0.85,
        counterfactual_score=50.0,
        eliminated=False,
    )
    diag = CompetingDiagnosisResult(
        hypotheses=[h_nan, h_inf, h_valid],
        selected_hypothesis_idx=2,
        selected_hypothesis=h_valid,
        repair_sites=h_valid.affected_sites,
        defect_confidence=0.85,
    )
    contract = APIContract(target_symbol="func")

    res = HypothesisContractGate.evaluate_and_filter(diag, contract)

    assert res.selected_hypothesis is not None
    assert not math.isnan(res.defect_confidence)
    assert not math.isinf(res.defect_confidence)
    assert res.selected_hypothesis.id == "C"
