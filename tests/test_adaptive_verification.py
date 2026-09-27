"""Unit tests for Adaptive Verification and Failure-Driven Replanning."""
from __future__ import annotations

from patchforge.repair.replanner import FailureDrivenReplanner, ReplanAction
from patchforge.verification.adaptive import AdaptiveVerifier, VerificationTier
from patchforge.verification.classifier import FailureClass


def test_failure_driven_replanner_decisions():
    # 1. Schema failure -> Fallback syntax
    d1 = FailureDrivenReplanner.decide(
        failure_class=FailureClass.REPAIR_SCHEMA_FAILURE.value,
        attempt=1,
        max_attempts=3,
    )
    assert d1.action == ReplanAction.FALLBACK_SYNTAX

    # 2. Regression -> Constrain mutation
    d2 = FailureDrivenReplanner.decide(
        failure_class=FailureClass.REGRESSION.value,
        attempt=1,
        max_attempts=3,
        p2p_failed=2,
    )
    assert d2.action == ReplanAction.CONSTRAIN_MUTATION

    # 3. Wrong hypothesis -> Switch hypothesis
    d3 = FailureDrivenReplanner.decide(
        failure_class=FailureClass.WRONG_HYPOTHESIS.value,
        attempt=1,
        max_attempts=3,
        hypotheses_available=3,
        active_hypothesis_idx=0,
    )
    assert d3.action == ReplanAction.SWITCH_HYPOTHESIS
    assert d3.target_hypothesis_id == "B"

    # 4. Partial semantic fix -> Refine semantics
    d4 = FailureDrivenReplanner.decide(
        failure_class=FailureClass.PATCH_SEMANTICS.value,
        attempt=2,
        max_attempts=3,
        f2p_passed=7,
    )
    assert d4.action == ReplanAction.REFINE_SEMANTICS
    assert "7 F2P passed" in d4.reason


def test_adaptive_verifier_static_failure():
    verifier = AdaptiveVerifier(tester=None)
    # When static validator encounters syntax error
    from patchforge.repair.schema import ReconstructedPatch
    from patchforge.repair.schema import RepairUnit, RepairUnitType

    u = RepairUnit(id="u1", file_path="t.py", symbol="t", unit_type=RepairUnitType.STATEMENT, node_type="Pass", start_line=1, end_line=1)
    rec = ReconstructedPatch(
        success=True,
        files_changed=["t.py"],
        patch_text="diff",
        modified_contents={"t.py": "def invalid syntax"},
    )
    verdict = verifier.verify("test-1", rec, {"t.py": "def valid(): pass\n"}, rec, [u])
    assert verdict.tier_reached == VerificationTier.STATIC
    assert not verdict.static_valid
    assert verdict.failure_class == FailureClass.REPAIR_VALIDATION_FAILURE.value
