"""Residual Failure Diagnosis & Behavioral Repair Completeness for PatchForge AI V1.7.

Tracks assertions transitioning from failing to passing, isolates residual failures,
and synthesizes minimal corrective guidance without discarding partial progress.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ResidualFailureRecord:
    """Structured record of residual failure after behavioral verification."""
    instance_id: str
    attempt: int
    f2p_passed_tests: List[str] = field(default_factory=list)
    f2p_failed_tests: List[str] = field(default_factory=list)
    f2p_passed_count: int = 0
    f2p_total_count: int = 0
    p2p_passed_count: int = 0
    p2p_total_count: int = 0
    residual_error_messages: List[str] = field(default_factory=list)
    unsatisfied_requirements: List[str] = field(default_factory=list)
    responsible_patch_text: str = ""
    responsible_target: str = ""
    is_partial_progress: bool = False
    has_regression: bool = False

    @property
    def coverage_percentage(self) -> float:
        if self.f2p_total_count <= 0:
            return 0.0
        return round((self.f2p_passed_count / self.f2p_total_count) * 100.0, 1)

    @classmethod
    def from_verdict(
        cls,
        instance_id: str,
        attempt: int,
        verdict: Any,
        patch_text: str = "",
        target_str: str = "",
        req_matrix: Optional[Any] = None,
    ) -> ResidualFailureRecord:
        """Constructs residual failure record from adaptive verification verdict."""
        f2p_p = getattr(verdict, "f2p_passed", 0)
        f2p_t = getattr(verdict, "f2p_total", 0)
        p2p_p = getattr(verdict, "p2p_passed", 0)
        p2p_t = getattr(verdict, "p2p_total", 0)

        ts = getattr(verdict, "test_summary", {}) or {}
        f2p_info = ts.get("fail_to_pass", {})
        f2p_success_list = ts.get("fail_to_pass_success", [])
        if not f2p_success_list and isinstance(f2p_info, dict):
            f2p_success_list = f2p_info.get("success", [])

        f2p_fail_list = ts.get("fail_to_pass_failure", [])
        if not f2p_fail_list and isinstance(f2p_info, dict):
            f2p_fail_list = f2p_info.get("failure", [])

        # Extract residual errors
        err_msgs: List[str] = []
        raw_err = getattr(verdict, "error_message", "")
        if raw_err:
            err_msgs.append(raw_err)

        output_text = ts.get("test_output", "") or ts.get("stderr", "") or ts.get("stdout", "")
        if output_text:
            for line in str(output_text).splitlines():
                line_s = line.strip()
                if any(k in line_s for k in ("FAIL:", "FAILED", "AssertionError", "Error:", "TypeError", "Exception:")):
                    if line_s not in err_msgs:
                        err_msgs.append(line_s)
                    if len(err_msgs) >= 5:
                        break

        # Check unsatisfied requirements
        unsatisfied_reqs: List[str] = []
        if req_matrix and hasattr(req_matrix, "requirements"):
            for req in req_matrix.requirements:
                if not getattr(req, "satisfied", True):
                    desc = getattr(req, "description", "") or getattr(req, "id", "")
                    unsatisfied_reqs.append(desc)

        has_reg = (p2p_t > p2p_p) if p2p_t > 0 else False
        is_partial = (f2p_p > 0 and f2p_p < f2p_t and not has_reg) or (f2p_p > 0 and not getattr(verdict, "resolved", False) and not has_reg)

        return cls(
            instance_id=instance_id,
            attempt=attempt,
            f2p_passed_tests=[str(t) for t in f2p_success_list],
            f2p_failed_tests=[str(t) for t in f2p_fail_list],
            f2p_passed_count=f2p_p,
            f2p_total_count=f2p_t,
            p2p_passed_count=p2p_p,
            p2p_total_count=p2p_t,
            residual_error_messages=err_msgs[:5],
            unsatisfied_requirements=unsatisfied_reqs[:5],
            responsible_patch_text=patch_text,
            responsible_target=target_str,
            is_partial_progress=is_partial,
            has_regression=has_reg,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "attempt": self.attempt,
            "f2p_passed_count": self.f2p_passed_count,
            "f2p_total_count": self.f2p_total_count,
            "f2p_passed_tests": self.f2p_passed_tests,
            "f2p_failed_tests": self.f2p_failed_tests,
            "coverage_percentage": self.coverage_percentage,
            "is_partial_progress": self.is_partial_progress,
            "has_regression": self.has_regression,
            "residual_error_messages": self.residual_error_messages,
            "unsatisfied_requirements": self.unsatisfied_requirements,
            "responsible_target": self.responsible_target,
        }

    def format_corrective_guidance(self) -> str:
        """Formats actionable corrective guidance for minimal residual repair."""
        lines = [
            f"PARTIAL REPAIR PROGRESS DIAGNOSIS (Attempt {self.attempt}):",
            f"- Succeeded on {self.f2p_passed_count} of {self.f2p_total_count} required assertions ({self.coverage_percentage:.1f}% coverage).",
            "- PRESERVE THESE PASSING ASSERTIONS (DO NOT REVERT OR REGRESS THEM):",
        ]
        if self.f2p_passed_tests:
            for t in self.f2p_passed_tests[:5]:
                lines.append(f"    * {t}")
        else:
            lines.append("    * (Underlying invariants verified passing)")

        lines.append("- RESIDUAL FAILING ASSERTIONS (TARGET STRICTLY THESE REMAINING DEFECTS):")
        if self.f2p_failed_tests:
            for t in self.f2p_failed_tests[:5]:
                lines.append(f"    * {t}")
        else:
            lines.append("    * (Remaining edge case in target specification)")

        if self.residual_error_messages:
            lines.append("- RESIDUAL ERROR DETAILS:")
            for err in self.residual_error_messages[:3]:
                lines.append(f"    * {err}")

        if self.unsatisfied_requirements:
            lines.append("- REMAINING UNSATISFIED REQUIREMENTS:")
            for req in self.unsatisfied_requirements[:3]:
                lines.append(f"    * {req}")

        lines.append(
            "MANDATORY INSTRUCTION: Do NOT rewrite your implementation from scratch. "
            "Preserve your working logic and make ONLY the minimal targeted edit necessary "
            "to fulfill the residual failing assertion."
        )
        return "\n".join(lines)
