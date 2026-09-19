"""Explicit, serializable AgentState for PatchForge v0.3."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from patchforge.issue.problem import Problem
from patchforge.reasoning.hypothesis import Hypothesis
from patchforge.reasoning.plan import RepairPlan
from patchforge.reasoning.probes import ProbeResult
from patchforge.reasoning.specification import RepairSpecification
from patchforge.reasoning.understanding import ProgramUnderstanding
from patchforge.repair.patch import Patch
from patchforge.retrieval.evidence import Evidence


class AgentPhase(str, Enum):
    INVESTIGATE = "INVESTIGATE"
    SPECIFY = "SPECIFY"
    UNDERSTAND = "UNDERSTAND"
    HYPOTHESIZE = "HYPOTHESIZE"
    VALIDATE = "VALIDATE"
    PLAN = "PLAN"
    PATCH = "PATCH"
    TEST = "TEST"
    EXECUTE = "EXECUTE"
    DIAGNOSE = "DIAGNOSE"
    REFINE = "REFINE"
    DONE = "DONE"
    FAILED = "FAILED"



@dataclass
class ExecutionEvidence:
    """Structured execution failure information feeding back into reasoning."""
    failing_tests: list[str] = field(default_factory=list)
    traceback: str = ""
    assertion_diff: str = ""
    exit_code: int = 1
    fail_to_pass_passed: int = 0
    fail_to_pass_total: int = 0
    pass_to_pass_regressions: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "failing_tests": self.failing_tests,
            "traceback": self.traceback[:1000],
            "assertion_diff": self.assertion_diff[:500],
            "exit_code": self.exit_code,
            "fail_to_pass_passed": self.fail_to_pass_passed,
            "fail_to_pass_total": self.fail_to_pass_total,
            "pass_to_pass_regressions": self.pass_to_pass_regressions,
        }


@dataclass
class AgentState:
    """Mutable and serializable state tracking the entire debugging session."""
    instance_id: str
    problem: Problem
    repo_dir: str = "."
    phase: AgentPhase = AgentPhase.INVESTIGATE
    turn_count: int = 0
    max_turns: int = 15

    # Navigation & Exploration
    visited_files: list[str] = field(default_factory=list)
    pinned_snippets: dict[str, str] = field(default_factory=dict)
    visited_symbols: list[str] = field(default_factory=list)

    # Evidence & Understanding
    evidence_store: list[Evidence] = field(default_factory=list)
    understanding: ProgramUnderstanding | None = None
    specification: RepairSpecification | None = None

    # Hypotheses & Validation
    hypotheses: list[Hypothesis] = field(default_factory=list)
    active_hypothesis: Hypothesis | None = None
    probe_results: list[ProbeResult] = field(default_factory=list)
    active_plan: RepairPlan | None = None

    # Patching & Execution
    active_patch: Patch | None = None
    patch_attempts: list[Patch] = field(default_factory=list)
    best_patch: Patch | None = None
    best_score: float = 0.0

    # Execution Feedback
    last_execution_evidence: ExecutionEvidence | None = None
    last_test_output: str = ""

    # External adapters
    repograph_adapter: Any = None
    tester: Any = None

    # Telemetry
    total_cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0

    # Verification & Patch Tracking (v0.3 controller fix)
    verified_read_files: list[str] = field(default_factory=list)
    last_failed_patch_file: str = ""
    invalid_paths: list[str] = field(default_factory=list)
    passive_patch_turns: int = 0

    # Phase Control-Flow Tracking & Guard Telemetry
    localization_turn_count: int = 0
    localization_transition_trigger: str = ""  # "CONFIDENCE_THRESHOLD" | "CIRCUIT_BREAKER_TURN_LIMIT" | "MODEL_VOLUNTARY"
    zero_info_turn_streak: int = 0
    phase_turn_counts: dict[str, int] = field(default_factory=dict)
    guard_firings: dict[str, int] = field(default_factory=lambda: {
        "exact_repetition_blocks": 0,
        "zero_info_blocks": 0,
        "confidence_transitions": 0,
        "circuit_breaker_transitions": 0,
        "loop_trap_blocks": 0,
        "phase_progression_transitions": 0,
        "unverified_patch_blocks": 0,
        "search_retry_blocks": 0,
        "invalid_path_blocks": 0,
        "passive_patch_recovery": 0,
    })
    recent_tool_calls: list[dict[str, Any]] = field(default_factory=list)


    def add_evidence(self, ev: Evidence) -> None:
        # Avoid exact duplicate evidence
        for existing in self.evidence_store:
            if existing.file == ev.file and existing.symbol == ev.symbol and existing.explanation == ev.explanation:
                return
        self.evidence_store.append(ev)

    def distinct_evidence_count(self) -> int:
        """Count distinct evidence items based on unique (file, symbol, type) combinations."""
        seen = set()
        for ev in self.evidence_store:
            key = (ev.file or "", ev.symbol or "", ev.type or "")
            seen.add(key)
        return len(seen)

    def record_file_visit(self, file_path: str, snippet: str = "") -> None:
        """Record visit to file and update pinned snippets with the latest inspected content.

        Policy: Pinned snippets store the most recent inspected content per visited file
        (from read_file or search_code). Later reads of the same file overwrite previous
        snippets so the model always sees the latest line range and context in the prompt.
        """
        if file_path not in self.visited_files:
            self.visited_files.append(file_path)
        if snippet:
            self.pinned_snippets[file_path] = snippet

    def record_phase_turn(self, phase: AgentPhase | str) -> None:
        phase_str = phase.value if isinstance(phase, AgentPhase) else str(phase)
        self.phase_turn_counts[phase_str] = self.phase_turn_counts.get(phase_str, 0) + 1



    def to_dict(self) -> dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "phase": self.phase.value,
            "turn_count": self.turn_count,
            "visited_files": self.visited_files,
            "visited_symbols": self.visited_symbols,
            "evidence_count": len(self.evidence_store),
            "distinct_evidence_count": self.distinct_evidence_count(),
            "understanding": self.understanding.to_dict() if self.understanding else None,
            "specification": self.specification.to_dict() if self.specification else None,
            "hypotheses": [h.to_dict() for h in self.hypotheses],
            "active_hypothesis": self.active_hypothesis.to_dict() if self.active_hypothesis else None,
            "active_plan": self.active_plan.to_dict() if self.active_plan else None,
            "probe_results": [p.to_dict() for p in self.probe_results],
            "patch_attempts_count": len(self.patch_attempts),
            "best_patch_tier": self.best_patch.match_tier if self.best_patch else None,
            "total_cost_usd": self.total_cost_usd,
            "tokens": {"input": self.input_tokens, "output": self.output_tokens},
            "verified_read_files": list(self.verified_read_files),
            "last_failed_patch_file": self.last_failed_patch_file,
            "invalid_paths": list(self.invalid_paths),
            "localization_transition_trigger": self.localization_transition_trigger,
            "guard_firings": dict(self.guard_firings),
        }
