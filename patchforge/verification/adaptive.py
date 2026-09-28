"""Adaptive Tiered Verification: executes progressive validation gates based on patch risk and test results."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from patchforge.repair.schema import ReconstructedPatch, StaticValidationResult
from patchforge.repair.validator import StaticRepairValidator
from patchforge.verification.classifier import FailureClass, classify
from patchforge.verification.tester import Tester

logger = logging.getLogger(__name__)


class VerificationTier(str, Enum):
    STATIC = "STATIC"
    TARGETED_F2P = "TARGETED_F2P"
    REGRESSION_P2P = "REGRESSION_P2P"
    FULL_SUITE = "FULL_SUITE"


@dataclass
class AdaptiveVerificationVerdict:
    """Consolidated verdict across adaptive verification tiers."""
    tier_reached: VerificationTier
    static_valid: bool = False
    tests_executed: bool = False
    resolved: bool = False
    failure_class: str = FailureClass.UNRESOLVED.value
    f2p_passed: int = 0
    f2p_total: int = 0
    p2p_passed: int = 0
    p2p_total: int = 0
    error_message: str = ""
    test_summary: Dict[str, Any] = field(default_factory=dict)
    runtime_s: float = 0.0


class AdaptiveVerifier:
    """Manages stage-gated verification (Static Gate -> Targeted F2P -> Regression P2P)."""

    def __init__(self, tester: Optional[Tester] = None):
        self.tester = tester

    def verify(
        self,
        instance_id: str,
        patch: Any,  # Patch or ReconstructedPatch
        original_sources: Dict[str, str],
        reconstructed: Optional[ReconstructedPatch] = None,
        target_units: Optional[List[Any]] = None,
        contract: Optional[Any] = None,
    ) -> AdaptiveVerificationVerdict:
        """Executes adaptive verification tiers progressively."""
        t0 = time.time()
        patch_text = getattr(patch, "patch_text", "") or ""

        # Tier 1: Static Validation Gate
        if reconstructed and original_sources:
            val_res = StaticRepairValidator.validate(
                original_sources=original_sources,
                reconstructed=reconstructed,
                target_units=target_units or [],
                contract=contract,
            )
            if not val_res.valid:
                return AdaptiveVerificationVerdict(
                    tier_reached=VerificationTier.STATIC,
                    static_valid=False,
                    failure_class=FailureClass.REPAIR_VALIDATION_FAILURE.value,
                    error_message="; ".join(val_res.errors),
                    runtime_s=round(time.time() - t0, 2),
                )

        if not self.tester:
            return AdaptiveVerificationVerdict(
                tier_reached=VerificationTier.STATIC,
                static_valid=True,
                failure_class="NO_TESTER",
                runtime_s=round(time.time() - t0, 2),
            )

        # Tier 2 & 3: Targeted Docker Execution
        verdict = self.tester.run(instance_id, patch_text)
        resolved = bool(getattr(verdict, "resolved", False))
        f_class = classify(patch, verdict)

        f2p_p = getattr(verdict, "fail_to_pass_passed", 0)
        f2p_t = getattr(verdict, "fail_to_pass_total", 0)
        p2p_p = getattr(verdict, "pass_to_pass_passed", 0)
        p2p_t = getattr(verdict, "pass_to_pass_total", 0)

        tier = VerificationTier.REGRESSION_P2P if (f2p_p > 0 or resolved) else VerificationTier.TARGETED_F2P
        summary = verdict.to_dict() if hasattr(verdict, "to_dict") else {}

        err_msg = getattr(verdict, "error", "") or ""
        if not err_msg:
            f2p_info = summary.get("fail_to_pass", {})
            f2p_fail_list = f2p_info.get("failure", []) if isinstance(f2p_info, dict) else []
            if f2p_fail_list:
                err_msg = f"Failing F2P test(s): {', '.join(f2p_fail_list[:3])}"
            elif getattr(verdict, "output", ""):
                lines = str(getattr(verdict, "output", "")).splitlines()
                err_lines = [l.strip() for l in lines if any(k in l for k in ("FAIL:", "FAILED", "AssertionError", "Error:"))]
                if err_lines:
                    err_msg = "; ".join(err_lines[-2:])

        return AdaptiveVerificationVerdict(
            tier_reached=tier,
            static_valid=True,
            tests_executed=True,
            resolved=resolved,
            failure_class=f_class.value,
            f2p_passed=f2p_p,
            f2p_total=f2p_t,
            p2p_passed=p2p_p,
            p2p_total=p2p_t,
            error_message=err_msg,
            test_summary=summary,
            runtime_s=round(time.time() - t0, 2),
        )
