"""Failure classification. Every unsuccessful patch gets a specific cause,
never a generic 'model failure'."""
from __future__ import annotations

from enum import Enum

from patchforge.localization.candidate import Candidate
from patchforge.repair.patch import Patch


class FailureClass(str, Enum):
    RESOLVED = "RESOLVED"
    UNRESOLVED = "UNRESOLVED"
    WRONG_LOCALIZATION = "WRONG_LOCALIZATION"
    WRONG_HYPOTHESIS = "WRONG_HYPOTHESIS"
    PATCH_SYNTAX = "PATCH_SYNTAX"
    PATCH_SEMANTICS = "PATCH_SEMANTICS"
    TEST_FAILURE = "TEST_FAILURE"
    REGRESSION = "REGRESSION"
    TIMEOUT = "TIMEOUT"
    INFRA_FAILURE = "INFRA_FAILURE"


def classify(patch: Patch, eval_result, candidates: list[Candidate] | None = None) -> FailureClass:
    """eval_result: integrations.swebench.EvalResult (duck-typed)."""
    if eval_result.infra_failure or (eval_result.error and "timeout" in eval_result.error.lower()):
        if "timeout" in eval_result.error.lower():
            return FailureClass.TIMEOUT
        return FailureClass.INFRA_FAILURE
    if eval_result.error and not getattr(eval_result, "patch_applied", True):
        return FailureClass.INFRA_FAILURE
    if not patch.valid or not patch.patch_text.strip():
        return FailureClass.PATCH_SYNTAX
    if not getattr(eval_result, "patch_applied", True):
        return FailureClass.PATCH_SYNTAX
    if eval_result.resolved:
        return FailureClass.RESOLVED
    f2p_total = eval_result.fail_to_pass_total
    f2p_ok = eval_result.fail_to_pass_passed
    p2p_total = eval_result.pass_to_pass_total
    p2p_ok = eval_result.pass_to_pass_passed
    if f2p_total and f2p_ok == f2p_total and p2p_ok < p2p_total:
        return FailureClass.REGRESSION
    if f2p_total and 0 < f2p_ok < f2p_total:
        return FailureClass.PATCH_SEMANTICS
    if f2p_total and f2p_ok == 0:
        if candidates is not None and patch.files_changed:
            top_files = {c.file for c in candidates[:5]}
            if not set(patch.files_changed) & top_files:
                return FailureClass.WRONG_LOCALIZATION
        return FailureClass.WRONG_HYPOTHESIS
    return FailureClass.TEST_FAILURE
