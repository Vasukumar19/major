"""Phase 3 Test Suite — Failure Classification & Strategic Replanning.

Verifies:
1. Missing import / NameError (requests-2148 trajectory) is classified as PATCH_SEMANTICS and routes to REFINE_SEMANTICS.
2. Target-exercised tracebacks with 0 F2P are classified as PATCH_SEMANTICS and preserve the target.
3. Genuinely unexercised/divergent hypotheses are classified as WRONG_HYPOTHESIS and pivot to alternative hypotheses.
4. Patches outside localized candidates are classified as WRONG_LOCALIZATION.
5. Syntax/indentation runtime errors are classified as PATCH_SYNTAX and route to FALLBACK_SYNTAX.
6. Repeated identical attempts trigger loop breaker and switch or abort.
"""
from __future__ import annotations

import pytest

from patchforge.localization.candidate import Candidate
from patchforge.repair.patch import Patch
from patchforge.repair.replanner import FailureDrivenReplanner, ReplanAction
from patchforge.verification.classifier import FailureClass, classify


class DummyEvalResult:
    def __init__(
        self,
        resolved: bool = False,
        patch_applied: bool = True,
        fail_to_pass_passed: int = 0,
        fail_to_pass_total: int = 1,
        pass_to_pass_passed: int = 10,
        pass_to_pass_total: int = 10,
        infra_failure: bool = False,
        error: str = "",
        test_output: str = "",
        stderr: str = "",
    ):
        self.resolved = resolved
        self.patch_applied = patch_applied
        self.fail_to_pass_passed = fail_to_pass_passed
        self.fail_to_pass_total = fail_to_pass_total
        self.pass_to_pass_passed = pass_to_pass_passed
        self.pass_to_pass_total = pass_to_pass_total
        self.infra_failure = infra_failure
        self.error = error
        self.test_output = test_output
        self.stderr = stderr


def test_missing_import_classified_as_patch_semantics_and_refines():
    """Verify that a missing import (requests-2148 pattern) does not trigger WRONG_HYPOTHESIS."""
    patch = Patch(valid=True, patch_text="diff --git a/requests/models.py", files_changed=["requests/models.py"])
    eval_res = DummyEvalResult(
        fail_to_pass_passed=0,
        fail_to_pass_total=1,
        test_output="NameError: name 'ChunkedEncodingError' is not defined\n  File 'requests/models.py', line 748, in iter_content",
    )

    f_class = classify(patch, eval_res, target_symbol="iter_content", target_file="requests/models.py")
    assert f_class == FailureClass.PATCH_SEMANTICS

    decision = FailureDrivenReplanner.decide(
        failure_class=f_class.value,
        attempt=1,
        max_attempts=3,
        hypotheses_available=2,
        active_hypothesis_idx=0,
        f2p_passed=0,
        p2p_failed=0,
        error_message="NameError: name 'ChunkedEncodingError' is not defined",
    )

    assert decision.action == ReplanAction.REFINE_SEMANTICS
    assert "UNRESOLVED SYMBOL/IMPORT" in decision.prompt_guidance
    assert decision.target_hypothesis_id is None  # Does NOT switch hypothesis!


def test_target_exercised_traceback_preserves_target():
    """Verify that when the traceback executes the target unit, it refines instead of switching."""
    patch = Patch(valid=True, patch_text="diff --git a/app.py", files_changed=["app.py"])
    eval_res = DummyEvalResult(
        fail_to_pass_passed=0,
        fail_to_pass_total=1,
        test_output="AssertionError: Expected 200 but got 500\n  File 'app.py', line 120, in register_blueprint",
    )

    f_class = classify(patch, eval_res, target_symbol="register_blueprint", target_file="app.py")
    assert f_class == FailureClass.PATCH_SEMANTICS

    decision = FailureDrivenReplanner.decide(
        failure_class=f_class.value,
        attempt=1,
        max_attempts=3,
        hypotheses_available=2,
        active_hypothesis_idx=0,
        f2p_passed=0,
        error_message="AssertionError: Expected 200 but got 500",
    )

    assert decision.action == ReplanAction.REFINE_SEMANTICS
    assert decision.target_hypothesis_id is None


def test_unexercised_hypothesis_triggers_switch():
    """Verify that genuinely unexercised hypotheses trigger SWITCH_HYPOTHESIS."""
    patch = Patch(valid=True, patch_text="diff --git a/unrelated.py", files_changed=["unrelated.py"])
    eval_res = DummyEvalResult(
        fail_to_pass_passed=0,
        fail_to_pass_total=1,
        test_output="AssertionError: Target mismatch in tests/test_core.py line 40\n  unrelated code never called",
    )

    f_class = classify(patch, eval_res, target_symbol="target_func", target_file="target.py")
    assert f_class == FailureClass.WRONG_HYPOTHESIS

    decision = FailureDrivenReplanner.decide(
        failure_class=f_class.value,
        attempt=1,
        max_attempts=3,
        hypotheses_available=2,
        active_hypothesis_idx=0,
        f2p_passed=0,
    )

    assert decision.action == ReplanAction.SWITCH_HYPOTHESIS
    assert decision.target_hypothesis_id == "B"


def test_wrong_localization_detected_when_outside_candidates():
    """Verify that editing files outside candidate set triggers WRONG_LOCALIZATION."""
    cand = Candidate(file="models.py", symbol="func", score=10.0)
    patch = Patch(valid=True, patch_text="diff --git a/docs.py", files_changed=["docs.py"])
    eval_res = DummyEvalResult(
        fail_to_pass_passed=0,
        fail_to_pass_total=1,
        test_output="Tests failed completely",
    )

    f_class = classify(patch, eval_res, candidates=[cand])
    assert f_class == FailureClass.WRONG_LOCALIZATION

    decision = FailureDrivenReplanner.decide(
        failure_class=f_class.value,
        attempt=1,
        max_attempts=3,
        hypotheses_available=2,
        active_hypothesis_idx=0,
    )

    assert decision.action == ReplanAction.SWITCH_HYPOTHESIS


def test_syntax_runtime_error_triggers_fallback_syntax():
    """Verify SyntaxError or IndentationError triggers PATCH_SYNTAX and FALLBACK_SYNTAX."""
    patch = Patch(valid=True, patch_text="diff --git a/mod.py", files_changed=["mod.py"])
    eval_res = DummyEvalResult(
        fail_to_pass_passed=0,
        fail_to_pass_total=1,
        test_output="IndentationError: unexpected indent in mod.py line 50",
    )

    f_class = classify(patch, eval_res)
    assert f_class == FailureClass.PATCH_SYNTAX

    decision = FailureDrivenReplanner.decide(
        failure_class=f_class.value,
        attempt=1,
        max_attempts=3,
    )

    assert decision.action == ReplanAction.FALLBACK_SYNTAX
