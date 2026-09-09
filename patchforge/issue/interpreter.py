"""Issue interpretation (Phase 1: deterministic structuring, no LLM).

Packages exactly what the agent may see: statement, hints, failing-test
ids. Never touches gold patch / hidden tests (Problem cannot carry them).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from patchforge.issue.problem import Problem


@dataclass
class InterpretedIssue:
    instance_id: str = ""
    statement: str = ""
    hints: str = ""
    failing_tests: list[str] = field(default_factory=list)
    relevant_tests: list[str] = field(default_factory=list)


class IssueInterpreter:
    def __init__(self, max_statement_chars: int = 6000):
        self.max_statement_chars = max_statement_chars

    def interpret(self, problem: Problem) -> InterpretedIssue:
        return InterpretedIssue(
            instance_id=problem.instance_id,
            statement=problem.problem_statement.strip()[: self.max_statement_chars],
            hints=problem.hints_text.strip()[:1000],
            failing_tests=list(problem.fail_to_pass),
            relevant_tests=list(problem.pass_to_pass[:20]),
        )
