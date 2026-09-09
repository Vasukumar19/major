"""Shared mutable repair state for one task."""
from __future__ import annotations

from dataclasses import dataclass, field

from patchforge.issue.problem import Problem
from patchforge.localization.candidate import Candidate
from patchforge.reasoning.hypothesis import Hypothesis


@dataclass
class RepairState:
    problem: Problem | None = None
    repo_dir: str = ""
    graph_pkl: str = ""
    candidates: list[Candidate] = field(default_factory=list)
    hypotheses: list[Hypothesis] = field(default_factory=list)
    attempts: list[dict] = field(default_factory=list)
    resolved: bool = False
    deadline_s: float = 0.0
