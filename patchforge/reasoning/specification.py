"""Structured RepairSpecification model for PatchForge v0.3."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from patchforge.issue.problem import Problem
from patchforge.reasoning.understanding import ProgramUnderstanding


@dataclass
class RepairSpecification:
    """Explicit behavioral specification and constraints for the repair."""
    expected_behavior: str = ""
    observed_behavior: str = ""
    constraints: list[str] = field(default_factory=list)
    edge_cases: list[str] = field(default_factory=list)
    non_goals: list[str] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)
    target_tests: list[str] = field(default_factory=list)
    must_preserve: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "expected_behavior": self.expected_behavior,
            "observed_behavior": self.observed_behavior,
            "constraints": self.constraints,
            "edge_cases": self.edge_cases,
            "non_goals": self.non_goals,
            "acceptance_criteria": self.acceptance_criteria,
            "target_tests": self.target_tests,
            "must_preserve": self.must_preserve,
        }

    def format_summary(self) -> str:
        lines = []
        if self.expected_behavior:
            lines.append(f"EXPECTED BEHAVIOR: {self.expected_behavior}")
        if self.observed_behavior:
            lines.append(f"OBSERVED BEHAVIOR: {self.observed_behavior}")
        if self.constraints:
            lines.append(f"CONSTRAINTS: {', '.join(self.constraints)}")
        if self.edge_cases:
            lines.append(f"EDGE CASES: {', '.join(self.edge_cases)}")
        if self.acceptance_criteria:
            lines.append(f"ACCEPTANCE CRITERIA: {', '.join(self.acceptance_criteria)}")
        if self.must_preserve:
            lines.append(f"MUST PRESERVE: {', '.join(self.must_preserve)}")
        return "\n".join(lines) if lines else "No specification details derived."


def derive_specification(problem: Problem, understanding: ProgramUnderstanding | None = None) -> RepairSpecification:
    """Synthesize a lightweight RepairSpecification from Problem and ProgramUnderstanding."""
    expected = understanding.expected_behavior if (understanding and understanding.expected_behavior) else "Code should satisfy issue requirements without regression."
    observed = understanding.observed_behavior if (understanding and understanding.observed_behavior) else (problem.problem_statement.strip().split("\n")[0][:200] if problem else "")
    constraints = list(understanding.violated_invariants) if (understanding and understanding.violated_invariants) else []
    
    target_tests = list(problem.fail_to_pass) if (problem and problem.fail_to_pass) else []
    must_preserve = list(problem.pass_to_pass[:5]) if (problem and problem.pass_to_pass) else ["Existing passing test cases"]

    return RepairSpecification(
        expected_behavior=expected,
        observed_behavior=observed,
        constraints=constraints,
        edge_cases=[],
        non_goals=[],
        acceptance_criteria=[f"Pass test: {t}" for t in target_tests] if target_tests else ["Satisfy problem specification"],
        target_tests=target_tests,
        must_preserve=must_preserve,
    )
