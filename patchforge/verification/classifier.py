"""Failure classification. Every unsuccessful patch gets a specific cause,
never a generic 'model failure'."""
from __future__ import annotations

from enum import Enum
import re

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
    # New V0.6 Structured Repair failure modes
    REPAIR_SCHEMA_FAILURE = "REPAIR_SCHEMA_FAILURE"
    REPAIR_TARGET_MISMATCH = "REPAIR_TARGET_MISMATCH"
    REPAIR_VALIDATION_FAILURE = "REPAIR_VALIDATION_FAILURE"
    REPAIR_OVEREXPANSION = "REPAIR_OVEREXPANSION"
    REPAIR_CONFLICT = "REPAIR_CONFLICT"


def classify(
    patch: Patch,
    eval_result,
    candidates: list[Candidate] | None = None,
    target_symbol: str | None = None,
    target_file: str | None = None,
) -> FailureClass:
    """eval_result: integrations.swebench.EvalResult (duck-typed)."""
    if eval_result.infra_failure or (eval_result.error and "timeout" in eval_result.error.lower()):
        if "timeout" in eval_result.error.lower():
            return FailureClass.TIMEOUT
        return FailureClass.INFRA_FAILURE
    if eval_result.error and not getattr(eval_result, "patch_applied", True):
        return FailureClass.INFRA_FAILURE
    if not patch.valid or not patch.patch_text.strip():
        if getattr(patch, "error", "") and "schema" in patch.error.lower():
            return FailureClass.REPAIR_SCHEMA_FAILURE
        if getattr(patch, "error", "") and "validation" in patch.error.lower():
            return FailureClass.REPAIR_VALIDATION_FAILURE
        if getattr(patch, "error", "") and "expansion" in patch.error.lower():
            return FailureClass.REPAIR_OVEREXPANSION
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
        output_text = f"{getattr(eval_result, 'test_output', '')} {getattr(eval_result, 'stderr', '')} {getattr(eval_result, 'error', '')}"

        # 1. Syntax / Indentation failure at runtime
        if re.search(r"\b(?:SyntaxError|IndentationError|TabError)\b", output_text):
            return FailureClass.PATCH_SYNTAX

        # 2. Missing imports / unresolved names / undefined symbols
        if (
            re.search(r"\b(?:NameError|ImportError|ModuleNotFoundError|UnboundLocalError)\b", output_text)
            or "is not defined" in output_text
            or "cannot import name" in output_text
        ):
            return FailureClass.PATCH_SEMANTICS

        # 3. Wrong localization: patch files changed vs top candidate files
        if candidates is not None and patch.files_changed:
            top_files = {getattr(c, "file", "") or getattr(c, "file_path", "") for c in candidates[:5]}
            top_files = {f for f in top_files if f}
            if top_files and not any(any(f in p_file or p_file in f for f in top_files) for p_file in patch.files_changed):
                return FailureClass.WRONG_LOCALIZATION

        # 4. Check if the traceback executed the patched files or target symbol
        files_to_check = list(patch.files_changed)
        if target_file:
            files_to_check.append(target_file)

        target_exercised = False
        for f in files_to_check:
            f_base = f.replace("\\", "/").split("/")[-1]
            if f_base and f_base in output_text:
                target_exercised = True
                break
        if target_symbol and target_symbol in output_text:
            target_exercised = True

        if target_exercised:
            # The failing tests actively called/exercised the target unit; the failure is in implementation semantics
            return FailureClass.PATCH_SEMANTICS

        return FailureClass.WRONG_HYPOTHESIS

    return FailureClass.TEST_FAILURE
