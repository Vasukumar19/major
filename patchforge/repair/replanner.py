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
    ) -> ReplanDecision:
        """Determines the next strategic action based on failure forensics."""
        if attempt >= max_attempts:
            return ReplanDecision(
                action=ReplanAction.ABORT,
                reason=f"Exhausted maximum retry budget ({max_attempts} attempts).",
            )

        # 1. Static Validation Gate Failure -> Refine with exact gate error feedback
        if failure_class == FailureClass.REPAIR_VALIDATION_FAILURE.value:
            return ReplanDecision(
                action=ReplanAction.REFINE_SEMANTICS,
                reason=f"Static validation gate rejected candidate: {error_message}",
                prompt_guidance=f"VALIDATION FAILURE: {error_message}. Your replacement MUST fix this violation.",
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

        # 2. Regression -> Constrain Mutation
        if p2p_failed > 0 or failure_class == FailureClass.REGRESSION.value:
            return ReplanDecision(
                action=ReplanAction.CONSTRAIN_MUTATION,
                reason="Regression detected: changes broke existing passing tests. Constrain repair blast radius.",
                prompt_guidance="Preserve existing callers and avoid altering public invariants or default arguments.",
            )

        # 3. Wrong Hypothesis / Zero F2P with alternatives available -> Switch Hypothesis
        if failure_class == FailureClass.WRONG_HYPOTHESIS.value and hypotheses_available > active_hypothesis_idx + 1:
            next_idx = active_hypothesis_idx + 1
            next_h_id = chr(65 + next_idx)
            return ReplanDecision(
                action=ReplanAction.SWITCH_HYPOTHESIS,
                reason=f"Current hypothesis failed to resolve defect. Pivoting to alternative Hypothesis {next_h_id}.",
                target_hypothesis_id=next_h_id,
            )

        # 4. Partial Semantic Fix (F2P > 0) -> Semantic Refinement
        if f2p_passed > 0 or failure_class == FailureClass.PATCH_SEMANTICS.value:
            return ReplanDecision(
                action=ReplanAction.REFINE_SEMANTICS,
                reason=f"Patch advanced test execution ({f2p_passed} F2P passed). Refining edge-case handling.",
                prompt_guidance=f"Inspect failing assertion and refine the conditional logic: {error_message[:200]}",
            )

        # Default: Refine semantics or expand context
        return ReplanDecision(
            action=ReplanAction.REFINE_SEMANTICS,
            reason="Unresolved test failure. Refining target repair logic.",
            prompt_guidance=error_message[:200],
        )
