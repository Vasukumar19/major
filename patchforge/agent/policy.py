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
        max_hypothesize_turns: int = 3,
        max_plan_turns: int = 2,
    ):
        self.max_turns = max_turns
        self.max_cost_usd = max_cost_usd
        self.max_patch_attempts = max_patch_attempts
        self.max_localization_turns = max_localization_turns
        self.target_confidence_threshold = target_confidence_threshold
        self.min_distinct_evidence = min_distinct_evidence
        self.max_zero_info_turns = max_zero_info_turns
        self.max_hypothesize_turns = max_hypothesize_turns
        self.max_plan_turns = max_plan_turns

    def evaluate_action(self, state: AgentState, tool_call: Any) -> ActionDecision:
        """Evaluate whether a proposed tool action is allowed or should be blocked/transitioned."""
        if tool_call is None:
            return ActionDecision(allowed=True)

        tool_name = getattr(tool_call, "name", "")
        tool_args = getattr(tool_call, "arguments", {}) or {}

        # 1. Layer A: Exact Signature Repetition Guard
        for recent in state.recent_tool_calls[-2:]:
            if recent.get("name") == tool_name and recent.get("arguments") == tool_args:
                state.guard_firings["exact_repetition_blocks"] = state.guard_firings.get("exact_repetition_blocks", 0) + 1
                return ActionDecision(
                    allowed=False,
                    blocked_reason=(
                        f"BLOCKED: You repeated an identical tool call '{tool_name}' with the exact same arguments "
                        "as a recent turn. You must choose a new action (e.g. read_file with specific lines, formulate a hypothesis, propose a plan, or apply_patch)."
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
                        "You must read the specific code lines, formulate a hypothesis, or propose a repair plan."
                    ),
                )

        # 3. Layer C: Phase-Aware Investigation Loop Guards
        if state.phase in (AgentPhase.HYPOTHESIZE, AgentPhase.PLAN, AgentPhase.PATCH):
            # Block searches once in plan/patch to prevent endless search loops
            if tool_name in ("search_code", "search_exact", "find_symbol", "find_references"):
                state.guard_firings["loop_trap_blocks"] = state.guard_firings.get("loop_trap_blocks", 0) + 1
                next_p = AgentPhase.PATCH if state.phase in (AgentPhase.PLAN, AgentPhase.PATCH) else AgentPhase.PLAN
                return ActionDecision(
                    allowed=False,
                    next_phase=next_p,
                    blocked_reason=(
                        f"BLOCKED: You are in {state.phase.value} phase and relevant target code is identified. "
                        "Do not run broad searches. Read specific lines or emit apply_patch."
                    ),
                )

        # 3b. Layer D: Verified Target Context & Patch Failure Recovery Guards
        if tool_name == "apply_patch":
            import re
            from pathlib import Path
            from patchforge.repair.generator import parse_edits

            patch_text = str(tool_args.get("patch_text", "") or "")
            if not patch_text and tool_args.get("file_path") and (tool_args.get("search") is not None or tool_args.get("replace") is not None):
                fp = tool_args.get("file_path")
                s = tool_args.get("search", "")
                r = tool_args.get("replace", "")
                patch_text = f"### {fp}\n<<<<<<< SEARCH\n{s}\n=======\n{r}\n>>>>>>> REPLACE"
                tool_args["patch_text"] = patch_text

            if not patch_text:
                return ActionDecision(
                    allowed=False,
                    blocked_reason="INVALID_TOOL_SCHEMA: Missing 'patch_text' or 'file_path'/'search'/'replace' arguments in apply_patch.",
                )

            # Check for unified diff / incompatible format
            if ("diff --git" in patch_text or "@@" in patch_text or "--- a/" in patch_text or "*** Begin" in patch_text) and ("<<<<<<< SEARCH" not in patch_text):
                state.guard_firings["wrong_patch_format_blocks"] = state.guard_firings.get("wrong_patch_format_blocks", 0) + 1
                return ActionDecision(
                    allowed=False,
                    blocked_reason=(
                        "WRONG_PATCH_FORMAT: You emitted unified diff syntax ('diff --git', '@@'). "
                        "Unified diff format is not accepted. You MUST use SEARCH/REPLACE block syntax:\n"
                        "### path/to/file.py\n<<<<<<< SEARCH\n<exact lines from file>\n=======\n<replacement lines>\n>>>>>>> REPLACE"
                    ),
                )

            edits = parse_edits(patch_text)
            if not edits:
                state.guard_firings["wrong_patch_format_blocks"] = state.guard_firings.get("wrong_patch_format_blocks", 0) + 1
                return ActionDecision(
                    allowed=False,
                    blocked_reason=(
                        "INVALID_TOOL_SCHEMA: Could not parse SEARCH/REPLACE blocks. Follow exact format:\n"
                        "### path/to/file.py\n<<<<<<< SEARCH\n    old_lines\n=======\n    new_lines\n>>>>>>> REPLACE"
                    ),
                )

            for target_file, blocks in edits.items():
                if target_file in state.invalid_paths:
                    state.guard_firings["invalid_path_blocks"] = state.guard_firings.get("invalid_path_blocks", 0) + 1
                    return ActionDecision(
                        allowed=False,
                        blocked_reason=(
                            f"BLOCKED: Target file '{target_file}' does not exist in the repository. "
                            "Use find_symbol or search_code to locate the real file path before applying a patch."
                        ),
                    )

                has_read_context = (
                    target_file in state.verified_read_files
                    or (target_file in state.pinned_snippets and len(state.pinned_snippets[target_file]) > 0)
                )
                if not has_read_context:
                    state.guard_firings["unverified_patch_blocks"] = state.guard_firings.get("unverified_patch_blocks", 0) + 1
                    return ActionDecision(
                        allowed=False,
                        blocked_reason=(
                            f"BLOCKED: You cannot apply a patch to '{target_file}' without inspecting its exact source context. "
                            f"UNVERIFIED_FILE: You MUST first call read_file(file_path='{target_file}', start_line=..., end_line=...) to see the exact verbatim lines."
                        ),
                    )

                if state.last_failed_patch_file == target_file:
                    state.guard_firings["search_retry_blocks"] = state.guard_firings.get("search_retry_blocks", 0) + 1
                    return ActionDecision(
                        allowed=False,
                        blocked_reason=(
                            f"BLOCKED: Your previous patch to '{target_file}' failed SEARCH matching. "
                            f"You MUST call read_file on '{target_file}' with line numbers to verify the exact current code lines before attempting another patch."
                        ),
                    )

                # Grounding validation against repository file
                repo_path = Path(state.repo_dir) / target_file
                if repo_path.exists():
                    try:
                        with open(repo_path, "r", encoding="utf-8", errors="replace") as f:
                            file_content = f.read()
                        for item in blocks:
                            s_block = item[0] if isinstance(item, tuple) else getattr(item, "search_block", "")
                            if s_block and s_block.strip() not in file_content:
                                state.guard_firings["search_not_found_blocks"] = state.guard_firings.get("search_not_found_blocks", 0) + 1
                                return ActionDecision(
                                    allowed=False,
                                    blocked_reason=(
                                        f"SEARCH_NOT_FOUND: The SEARCH block for '{target_file}' was not found verbatim in the file. "
                                        "Ensure you quote exact lines visible in MOST RECENT FILE CONTENT."
                                    ),
                                )
                    except Exception:
                        pass

        if tool_name == "read_file":
            fp = tool_args.get("file_path", "")
            if fp and fp in state.invalid_paths:
                state.guard_firings["invalid_path_blocks"] = state.guard_firings.get("invalid_path_blocks", 0) + 1
                return ActionDecision(
                    allowed=False,
                    blocked_reason=(
                        f"BLOCKED: File '{fp}' does not exist in the repository. "
                        "Use find_symbol or search_code to locate the real path."
                    ),
                )

        # 4. Phase Progression: Clean Confidence Exit vs Circuit Breaker from INVESTIGATE
        if state.phase == AgentPhase.INVESTIGATE:
            # 4a. Primary Path: Clean Confidence Exit
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

            # 4b. Fallback Path: Unconditional Circuit Breaker Turn Limit
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
        # Phase-specific turn budgets (non-terminating phase transitions)
        if state.phase == AgentPhase.HYPOTHESIZE and state.phase_turn_counts.get("HYPOTHESIZE", 0) >= self.max_hypothesize_turns:
            return PolicyDecision(
                allowed=True,
                should_terminate=False,
                next_phase=AgentPhase.PLAN,
                reason=f"Hypothesis phase turn limit ({self.max_hypothesize_turns}) reached. Transitioning to PLAN.",
            )
        if state.phase == AgentPhase.PLAN and state.phase_turn_counts.get("PLAN", 0) >= self.max_plan_turns:
            return PolicyDecision(
                allowed=True,
                should_terminate=False,
                next_phase=AgentPhase.PATCH,
                reason=f"Plan phase turn limit ({self.max_plan_turns}) reached. Transitioning to PATCH.",
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

        # Incomplete / failing: transition to REFINE to analyze failure evidence and revise patch
        return PolicyDecision(
            allowed=True,
            should_terminate=False,
            next_phase=AgentPhase.REFINE,
            reason="Test failed. Transitioning to REFINE to analyze failure evidence and revise patch.",
        )
