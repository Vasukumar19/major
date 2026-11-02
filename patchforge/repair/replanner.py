"""Failure-Driven Replanning: dispatches targeted replanning actions based on verification failure class."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

from patchforge.verification.classifier import FailureClass

logger = logging.getLogger(__name__)


class ReplanAction(str, Enum):
    FALLBACK_SYNTAX = "FALLBACK_SYNTAX"
    SWITCH_HYPOTHESIS = "SWITCH_HYPOTHESIS"
    REFINE_SEMANTICS = "REFINE_SEMANTICS"
    EXPAND_CONTEXT = "EXPAND_CONTEXT"
    CONSTRAIN_MUTATION = "CONSTRAIN_MUTATION"
    ABORT = "ABORT"


@dataclass
class ReplanDecision:
    action: ReplanAction
    reason: str
    target_hypothesis_id: Optional[str] = None
    suggested_symbol: Optional[str] = None
    prompt_guidance: str = ""


class FailureDrivenReplanner:
    """Evaluates verification outcomes to make informed architectural replanning decisions."""

    @classmethod
    def decide(
        cls,
        failure_class: str,
        attempt: int,
        max_attempts: int,
        hypotheses_available: int = 1,
        active_hypothesis_idx: int = 0,
        f2p_passed: int = 0,
        p2p_failed: int = 0,
        error_message: str = "",
        residual_record: Optional[Any] = None,
        repeated_attempt: bool = False,
        available_hypotheses: Optional[List[Any]] = None,
    ) -> ReplanDecision:
        """Determines the next strategic action based on failure forensics."""
        if attempt >= max_attempts:
            return ReplanDecision(
                action=ReplanAction.ABORT,
                reason=f"Exhausted maximum retry budget ({max_attempts} attempts).",
            )

        # 0. Repeated Ineffective Attempt Breaker (Priority D)
        if repeated_attempt:
            # Look for an alternative non-eliminated hypothesis
            next_h_id = None
            if available_hypotheses:
                for idx, h in enumerate(available_hypotheses):
                    if idx != active_hypothesis_idx and not getattr(h, "eliminated", False):
                        next_h_id = getattr(h, "id", None) or getattr(h, "name", None) or chr(65 + idx)
                        break
            elif hypotheses_available > active_hypothesis_idx + 1:
                next_h_id = chr(65 + active_hypothesis_idx + 1)

            if next_h_id:
                return ReplanDecision(
                    action=ReplanAction.SWITCH_HYPOTHESIS,
                    reason=f"Repeated ineffective attempt detected on active hypothesis index {active_hypothesis_idx}. Pivoting to alternative Hypothesis {next_h_id}.",
                    target_hypothesis_id=str(next_h_id),
                    prompt_guidance="PREVIOUS ATTEMPT WAS INEFFECTIVE/REPEATED: Pivot strictly to alternative hypothesis approach.",
                )
            if failure_class in (
                FailureClass.PATCH_SYNTAX.value,
                FailureClass.REPAIR_SCHEMA_FAILURE.value,
            ):
                return ReplanDecision(
                    action=ReplanAction.FALLBACK_SYNTAX,
                    reason="Repeated syntax/schema failure. Bypassing JSON wrapper with minimal replacement code.",
                    prompt_guidance="Focus strictly on emitting valid python code replacement without markdown wrappers.",
                )
            return ReplanDecision(
                action=ReplanAction.ABORT,
                reason="Repeated identical attempt detected with no alternative hypothesis remaining. Terminating unproductive retry loop honestly.",
            )

        # 1. Static Validation Gate Failure -> Refine with exact gate error feedback
        if failure_class == FailureClass.REPAIR_VALIDATION_FAILURE.value:
            return ReplanDecision(
                action=ReplanAction.REFINE_SEMANTICS,
                reason=f"Static validation gate rejected candidate: {error_message}",
                prompt_guidance=f"VALIDATION FAILURE: {error_message}. Your replacement MUST fix this violation.",
            )

        # 1b. No-op or Empty Patch / UNRESOLVED -> Insist on concrete modification
        if failure_class == FailureClass.UNRESOLVED.value or "No changes produced" in error_message or "no-op repair" in error_message:
            return ReplanDecision(
                action=ReplanAction.REFINE_SEMANTICS,
                reason="Patch candidate produced no modifications to the target code unit.",
                prompt_guidance="CRITICAL: Your replacement code was identical or produced no changes. You MUST introduce the necessary bug fix logic to modify the target code unit.",
            )

        # 2. Schema or Syntax Failure -> Protocol Fallback
        if failure_class in (
            FailureClass.PATCH_SYNTAX.value,
            FailureClass.REPAIR_SCHEMA_FAILURE.value,
        ):
            return ReplanDecision(
                action=ReplanAction.FALLBACK_SYNTAX,
                reason="Patch syntax or structured schema invalid. Using minimal replacement-only fallback prompt.",
                prompt_guidance="Focus strictly on emitting valid python code replacement without markdown wrappers.",
            )

        # 2b. Regression -> Constrain Mutation
        if p2p_failed > 0 or failure_class == FailureClass.REGRESSION.value:
            return ReplanDecision(
                action=ReplanAction.CONSTRAIN_MUTATION,
                reason="Regression detected: changes broke existing passing tests. Constrain repair blast radius.",
                prompt_guidance="Preserve existing callers and avoid altering public invariants or default arguments.",
            )

        # 3. Wrong Hypothesis / Wrong Localization -> Switch Hypothesis when alternatives exist
        if failure_class in (
            FailureClass.WRONG_HYPOTHESIS.value,
            FailureClass.WRONG_LOCALIZATION.value,
        ) and hypotheses_available > active_hypothesis_idx + 1:
            next_idx = active_hypothesis_idx + 1
            next_h_id = chr(65 + next_idx)
            return ReplanDecision(
                action=ReplanAction.SWITCH_HYPOTHESIS,
                reason=f"Current hypothesis failed to resolve defect ({failure_class}). Pivoting to alternative Hypothesis {next_h_id}.",
                target_hypothesis_id=next_h_id,
            )

        # 4. Partial Semantic Fix or Implementation Defect -> Behavioral Refinement Loop
        if (
            f2p_passed > 0
            or failure_class in (FailureClass.PATCH_SEMANTICS.value, FailureClass.TEST_FAILURE.value)
        ):
            if any(err_kw in error_message for err_kw in ["NameError", "ImportError", "ModuleNotFoundError", "is not defined", "cannot import name"]):
                guidance = (
                    f"IMPLEMENTATION DEFECT (UNRESOLVED SYMBOL/IMPORT): {error_message[:400]}. "
                    f"The target unit is correctly localized, but the patch contains an unresolved name or missing import. "
                    f"Ensure all required exceptions, helpers, and types are properly imported or defined within the scope."
                )
            elif residual_record and hasattr(residual_record, "format_corrective_guidance") and getattr(residual_record, "is_partial_progress", False):
                guidance = residual_record.format_corrective_guidance()
            elif f2p_passed > 0:
                guidance = (
                    f"BEHAVIORAL REPAIR COMPLETENESS: Your patch successfully passed {f2p_passed} test assertions, "
                    f"confirming your core hypothesis is directionally correct. DO NOT discard your logic or rewrite from scratch. "
                    f"You only need to complete the remaining failing assertion: {error_message[:800]}. "
                    f"Refine the conditional edge case while preserving your existing state transformation."
                )
            else:
                guidance = (
                    f"IMPLEMENTATION REFINEMENT: Verification failed with test errors: {error_message[:400]}. "
                    f"The target unit is maintained. Refine the logic to satisfy all expected test assertions while preserving backward compatibility."
                )
            reason = (
                f"Patch advanced test execution ({f2p_passed} F2P passed). Targeting remaining unfulfilled edge case."
                if f2p_passed > 0
                else f"Refining implementation on current target (failure: {failure_class})."
            )
            return ReplanDecision(
                action=ReplanAction.REFINE_SEMANTICS,
                reason=reason,
                prompt_guidance=guidance,
            )

        # Default: Refine semantics or expand context
        return ReplanDecision(
            action=ReplanAction.REFINE_SEMANTICS,
            reason="Unresolved test failure. Refining target repair logic.",
            prompt_guidance=error_message[:200],
        )
