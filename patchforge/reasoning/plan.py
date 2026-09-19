"""Structured RepairPlan model for PatchForge v0.3."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RepairPlan:
    """Concrete plan translating a hypothesis into actionable edits."""
    target_file: str = ""
    target_symbols: list[str] = field(default_factory=list)
    intended_change: str = ""
    rationale: str = ""
    validation_tests: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_file": self.target_file,
            "target_symbols": self.target_symbols,
            "intended_change": self.intended_change,
            "rationale": self.rationale,
            "validation_tests": self.validation_tests,
        }

    def format_summary(self) -> str:
        lines = []
        if self.target_file:
            lines.append(f"TARGET FILE: {self.target_file}")
        if self.target_symbols:
            lines.append(f"TARGET SYMBOLS: {', '.join(self.target_symbols)}")
        if self.intended_change:
            lines.append(f"INTENDED CHANGE: {self.intended_change}")
        if self.rationale:
            lines.append(f"RATIONALE: {self.rationale}")
        if self.validation_tests:
            lines.append(f"VALIDATION TESTS: {', '.join(self.validation_tests)}")
        return "\n".join(lines) if lines else "No repair plan details formulated."
