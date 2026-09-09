"""Context compactor for PatchForge v0.3 agentic sessions."""
from __future__ import annotations

from typing import Any

from patchforge.agent.state import AgentState
from patchforge.agent.trajectory import AgentTrajectory


class ContextCompactor:
    """Maintains a focused working memory and prunes redundant/stale context."""

    MAX_RAW_READ_CHARS = 24000
    MAX_RECENT_RAW_READS = 2

    def __init__(self, max_tokens_estimate: int = 4000):
        self.max_tokens_estimate = max_tokens_estimate

    def compact_prompt(self, state: AgentState, trajectory: AgentTrajectory) -> str:
        """Assemble a clean, compact context prompt from state and recent trajectory."""
        sections = []

        # 0. Active Agent Phase & Guidance
        sections.append(f"=== CURRENT AGENT PHASE: {state.phase.value} (Turn {state.turn_count}/{state.max_turns}) ===")
        if state.phase.value == "INVESTIGATE":
            sections.append("Objective: Locate the root cause file and symbol using search and read tools.")
        elif state.phase.value == "HYPOTHESIZE":
            sections.append("Objective: Target location is found. Analyze code invariants and formulate your fix hypothesis. You may read specific code lines or proceed to apply_patch.")
        elif state.phase.value == "PLAN":
            sections.append("Objective: Prepare specific edits and test cases before applying patch.")
        elif state.phase.value == "PATCH":
            sections.append("Objective: Apply the unified diff patch using apply_patch.")
        elif state.phase.value == "REFINE":
            sections.append("Objective: Execution failed or near-miss. Refine your patch based on the failure traceback.")

        # 1. Problem Statement
        sections.append("\n=== PROBLEM STATEMENT ===")
        sections.append(state.problem.problem_statement.strip()[:2000])

        # 2. Program Understanding (if derived)
        if state.understanding:
            sections.append("\n=== CURRENT PROGRAM UNDERSTANDING ===")
            sections.append(state.understanding.format_summary())

        # 3. Key Pinned Files & Visited Snippets
        if state.pinned_snippets:
            sections.append("\n=== RELEVANT CODE SNIPPETS ===")
            for file_path, snippet in list(state.pinned_snippets.items())[-3:]:
                sections.append(f"--- File: {file_path} ---\n{snippet}")

        # 4. Top Supporting Evidence
        if state.evidence_store:
            sections.append("\n=== KEY EVIDENCE ===")
            for ev in state.evidence_store[-6:]:
                sections.append(f"- [{ev.type}] {ev.file} :: {ev.symbol} -> {ev.explanation}")

        # 5. Active Hypothesis & Probe Results
        if state.active_hypothesis:
            sections.append(f"\n=== ACTIVE HYPOTHESIS: {state.active_hypothesis.id} ===")
            sections.append(state.active_hypothesis.description)
            sections.append(f"Expected Behavior: {state.active_hypothesis.expected_behavior}")

        if state.probe_results:
            sections.append("\n=== PROBE VALIDATION RESULTS ===")
            for pr in state.probe_results[-3:]:
                status = "SUPPORTS" if pr.supports else ("CONTRADICTS" if pr.contradicts else "NEUTRAL")
                sections.append(f"- [{pr.probe_type} - {status}] {pr.observation}")

        # 6. Latest Execution Evidence / Failure (if any)
        if state.last_execution_evidence:
            sections.append("\n=== LATEST EXECUTION FAILURE ===")
            ee = state.last_execution_evidence
            if ee.failing_tests:
                sections.append(f"Failing tests: {', '.join(ee.failing_tests[:4])}")
            if ee.traceback:
                sections.append(f"Traceback:\n{ee.traceback[:600]}")
            if ee.assertion_diff:
                sections.append(f"Assertion diff:\n{ee.assertion_diff[:300]}")

        # 7. Preserve the latest read results verbatim; summaries alone lose patch anchors.
        recent_reads = [
            step for step in trajectory.steps
            if step.action_name == "read_file"
            and step.tool_result.get("status") == "SUCCESS"
            and step.tool_result.get("data", {}).get("content")
        ][-self.MAX_RECENT_RAW_READS:]
        if recent_reads:
            sections.append("\n=== MOST RECENT FILE CONTENT (VERBATIM) ===")
            for step in recent_reads:
                content = step.tool_result["data"]["content"]
                if len(content) > self.MAX_RAW_READ_CHARS:
                    content = content[:self.MAX_RAW_READ_CHARS]
                    content += "\n[raw read content truncated at a line-safe defensive limit]"
                file_path = step.tool_result["data"].get("file_path", "")
                sections.append(f"--- Turn {step.turn}, File: {file_path} ---\n{content}")

        # 8. Recent Action-Observation Memory (Last 3 steps only to retain narrative continuity)
        recent_steps = trajectory.steps[-3:]
        if recent_steps:
            sections.append("\n=== RECENT TOOL ACTIONS & OBSERVATIONS ===")
            for step in recent_steps:
                sections.append(f"[Turn {step.turn}] Action: {step.action_name} -> {step.observation_summary[:250]}")

        return "\n".join(sections)
