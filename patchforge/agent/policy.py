"""Deterministic policy, safety guards, and termination bounds for PatchForge v0.3."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from patchforge.agent.state import AgentPhase, AgentState


@dataclass
class PolicyDecision:
    allowed: bool
    should_terminate: bool
    next_phase: AgentPhase | None = None
    reason: str = ""


@dataclass
class ActionDecision:
    allowed: bool
    blocked_reason: str = ""
    next_phase: AgentPhase | None = None
    transition_trigger: str = ""


class AgentPolicy:
    """Enforces turn limits, budget guards, anti-repetition guards, and deterministic phase transitions."""

    def __init__(
        self,
        max_turns: int = 15,
        max_cost_usd: float = 1.0,
        max_patch_attempts: int = 4,
        max_localization_turns: int = 4,
        target_confidence_threshold: float = 0.70,
        min_distinct_evidence: int = 2,
        max_zero_info_turns: int = 2,
    ):
        self.max_turns = max_turns
        self.max_cost_usd = max_cost_usd
        self.max_patch_attempts = max_patch_attempts
        self.max_localization_turns = max_localization_turns
        self.target_confidence_threshold = target_confidence_threshold
        self.min_distinct_evidence = min_distinct_evidence
        self.max_zero_info_turns = max_zero_info_turns

    def evaluate_action(self, state: AgentState, tool_call: Any) -> ActionDecision:
        """Evaluate whether a proposed tool action is allowed or should be blocked/transitioned."""
        if tool_call is None:
            return ActionDecision(allowed=True)

        tool_name = getattr(tool_call, "name", "")
        tool_args = getattr(tool_call, "arguments", {}) or {}

        # 1. Layer A: Exact Signature Repetition Guard
        # Check against the last 2 tool calls
        for recent in state.recent_tool_calls[-2:]:
            if recent.get("name") == tool_name and recent.get("arguments") == tool_args:
                state.guard_firings["exact_repetition_blocks"] = state.guard_firings.get("exact_repetition_blocks", 0) + 1
                return ActionDecision(
                    allowed=False,
                    blocked_reason=(
                        f"BLOCKED: You repeated an identical tool call '{tool_name}' with the exact same arguments "
                        "as a recent turn. You must choose a new action (e.g. read_file with specific lines, formulate a hypothesis, or apply_patch)."
                    ),
                )

        # 2. Layer B: Semantic Zero-Information Gain Guard
        if state.phase == AgentPhase.INVESTIGATE and state.zero_info_turn_streak >= self.max_zero_info_turns:
            if tool_name in ("search_code", "search_exact", "find_symbol"):
                state.guard_firings["zero_info_blocks"] = state.guard_firings.get("zero_info_blocks", 0) + 1
                return ActionDecision(
                    allowed=False,
                    blocked_reason=(
                        f"BLOCKED: No new code or structural information gained in the last {self.max_zero_info_turns} turns. "
                        "Target symbols and files are already identified in your working memory. "
                        "You must read the specific code lines, formulate a hypothesis, or apply a patch."
                    ),
                )

        # 3. Phase Progression: Clean Confidence Exit vs Circuit Breaker
        if state.phase == AgentPhase.INVESTIGATE:
            # 3a. Primary Path: Clean Confidence Exit
            has_high_confidence = (
                state.understanding is not None
                and state.understanding.confidence >= self.target_confidence_threshold
                and state.distinct_evidence_count() >= self.min_distinct_evidence
            )
            if has_high_confidence:
                state.localization_transition_trigger = "CONFIDENCE_THRESHOLD"
                state.guard_firings["confidence_transitions"] = state.guard_firings.get("confidence_transitions", 0) + 1
                state.phase = AgentPhase.HYPOTHESIZE
                if tool_name in ("search_code", "search_exact", "find_symbol"):
                    return ActionDecision(
                        allowed=False,
                        next_phase=AgentPhase.HYPOTHESIZE,
                        transition_trigger="CONFIDENCE_THRESHOLD",
                        blocked_reason=(
                            f"Target confidence threshold reached ({state.understanding.confidence:.2f} >= {self.target_confidence_threshold:.2f} "
                            f"with {state.distinct_evidence_count()} distinct evidence items). Localization complete. "
                            "Transitioned to HYPOTHESIZE phase. Formulate your causal hypothesis or read specific lines to patch."
                        ),
                    )
                return ActionDecision(
                    allowed=True,
                    next_phase=AgentPhase.HYPOTHESIZE,
                    transition_trigger="CONFIDENCE_THRESHOLD",
                )

            # 3b. Fallback Path: Unconditional Circuit Breaker Turn Limit
            if state.localization_turn_count >= self.max_localization_turns:
                state.localization_transition_trigger = "CIRCUIT_BREAKER_TURN_LIMIT"
                state.guard_firings["circuit_breaker_transitions"] = state.guard_firings.get("circuit_breaker_transitions", 0) + 1
                state.phase = AgentPhase.HYPOTHESIZE
                if tool_name in ("search_code", "search_exact", "find_symbol"):
                    return ActionDecision(
                        allowed=False,
                        next_phase=AgentPhase.HYPOTHESIZE,
                        transition_trigger="CIRCUIT_BREAKER_TURN_LIMIT",
                        blocked_reason=(
                            f"Reached maximum localization turns ({self.max_localization_turns}) circuit breaker. "
                            "Transitioned to HYPOTHESIZE phase. Synthesize available evidence into a repair hypothesis or read lines to patch."
                        ),
                    )
                return ActionDecision(
                    allowed=True,
                    next_phase=AgentPhase.HYPOTHESIZE,
                    transition_trigger="CIRCUIT_BREAKER_TURN_LIMIT",
                )

        return ActionDecision(allowed=True)

    def evaluate_budget(self, state: AgentState) -> PolicyDecision:
        """Check if turn count or budget limits have been reached."""
        if state.turn_count >= self.max_turns:
            return PolicyDecision(
                allowed=False,
                should_terminate=True,
                next_phase=AgentPhase.FAILED,
                reason=f"Reached maximum turn limit ({self.max_turns}).",
            )
        if state.total_cost_usd >= self.max_cost_usd:
            return PolicyDecision(
                allowed=False,
                should_terminate=True,
                next_phase=AgentPhase.FAILED,
                reason=f"Exceeded maximum session cost budget (${self.max_cost_usd:.2f}).",
            )
        if len(state.patch_attempts) >= self.max_patch_attempts:
            return PolicyDecision(
                allowed=False,
                should_terminate=True,
                next_phase=AgentPhase.FAILED,
                reason=f"Reached maximum patch attempt limit ({self.max_patch_attempts}).",
            )
        return PolicyDecision(allowed=True, should_terminate=False)

    def evaluate_execution_result(
        self, state: AgentState, passed: bool, fail_to_pass_passed: int, fail_to_pass_total: int, p2p_regressions: int
    ) -> PolicyDecision:
        """Determine next state after a test execution."""
        if passed:
            return PolicyDecision(
                allowed=True,
                should_terminate=True,
                next_phase=AgentPhase.DONE,
                reason="All tests passed (100% resolution).",
            )

        # Near miss check: F2P >= 50% and 0 regressions
        if fail_to_pass_total > 0:
            pass_ratio = fail_to_pass_passed / fail_to_pass_total
            if pass_ratio >= 0.5 and p2p_regressions == 0:
                return PolicyDecision(
                    allowed=True,
                    should_terminate=False,
                    next_phase=AgentPhase.REFINE,
                    reason=f"Near-miss detected ({fail_to_pass_passed}/{fail_to_pass_total} F2P, 0 regressions). Transitioning to REFINE.",
                )

        # Incomplete / failing: allow re-investigating or trying another hypothesis
        return PolicyDecision(
            allowed=True,
            should_terminate=False,
            next_phase=AgentPhase.INVESTIGATE,
            reason="Test failed. Returning to INVESTIGATE to gather new evidence or explore alternative hypotheses.",
        )
