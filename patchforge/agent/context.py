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

    def _build_repair_context(self, state: AgentState, trajectory: AgentTrajectory) -> str:
        """Construct isolated Repair Context (clean slate, no rambling investigation trajectory)."""
        import re
        target_f = (state.active_plan.target_file if state.active_plan else "") or state.last_failed_patch_file
        if not target_f and state.verified_read_files:
            target_f = state.verified_read_files[0]
        if not target_f and state.visited_files:
            target_f = state.visited_files[0]
        if not target_f and state.pinned_snippets:
            target_f = list(state.pinned_snippets.keys())[0]
        if not target_f:
            target_f = "target_file.py"

        target_sym = (state.active_plan.target_symbols[0] if (state.active_plan and state.active_plan.target_symbols) else "")
        if not target_sym and state.visited_symbols:
            target_sym = state.visited_symbols[0]

        # Extract verified source lines for target_f
        verified_source = ""
        if target_f in state.pinned_snippets:
            verified_source = state.pinned_snippets[target_f]
        elif target_f:
            for step in reversed(trajectory.steps):
                if step.action_name == "read_file":
                    res = step.tool_result
                    data = res.get("data", {}) if isinstance(res, dict) else (res.data if hasattr(res, "data") else {})
                    if data.get("file_path") == target_f and data.get("content"):
                        verified_source = data.get("content", "")
                        break
        if not verified_source and state.pinned_snippets:
            # Fallback to any pinned snippet if target_f wasn't directly found
            verified_source = list(state.pinned_snippets.values())[-1]

        sections = [
            f"=== CURRENT AGENT PHASE: PATCH (Turn {state.turn_count}/{state.max_turns}) ===",
            "=== PATCH CONTRACT ===",
            f"You are now in the PATCH phase. Target file '{target_f}' has been verified and source is provided below.",
            "CRITICAL DIRECTIVES:",
            "1. Do NOT explain the patch in prose, analysis, or natural language.",
            f"2. Your NEXT action MUST be an apply_patch JSON tool call targeting '{target_f}'.",
            "3. Use the exact source text visible in the VERIFIED SOURCE CONTEXT section below.",
            "4. Do NOT emit unified diff syntax ('diff --git', '@@', '--- a/', '+++ b/').",
            "5. Do NOT emit markdown diff syntax.",
            "6. The SEARCH block MUST exactly match verbatim lines visible in the verified source below.",
            "7. The REPLACE block contains your intended replacement code.",
            "8. Output a single valid JSON object:",
            "```json",
            "{",
            f'  "thought": "Applying SEARCH/REPLACE fix to {target_f}",',
            '  "action": {',
            '    "name": "apply_patch",',
            '    "arguments": {',
            f'      "patch_text": "### {target_f}\\n<<<<<<< SEARCH\\n<exact verbatim lines from verified source below>\\n=======\\n<replacement lines>\\n>>>>>>> REPLACE"',
            "    }",
            "  }",
            "}",
            "```",
            "",
            "=== REPAIR STATE ===",
            f"Issue: {state.problem.problem_statement.strip()[:1000]}",
            f"Target File: {target_f}",
            f"Target Symbol: {target_sym or 'Identified in plan'}",
            f"Active Hypothesis: {state.active_hypothesis.description if state.active_hypothesis else (state.understanding.summary if state.understanding else 'Bug fix')}",
            f"Active Plan: {state.active_plan.format_summary() if state.active_plan else 'Apply targeted bug fix'}",
        ]

        if state.last_execution_evidence:
            ee = state.last_execution_evidence
            sections.append(f"Previous Test Status: FAILED (exit code {ee.exit_code})")
            if ee.failing_tests:
                sections.append(f"Failing Tests: {', '.join(ee.failing_tests[:4])}")
            if ee.traceback:
                sections.append(f"Failure Traceback:\n{ee.traceback[:600]}")
        else:
            sections.append("Previous Test Status: NOT RUN")

        sections.append("\n=== VERIFIED SOURCE CONTEXT (VERBATIM) ===")
        if verified_source:
            denumbered = "\n".join(re.sub(r"^\s*\d+:\s?", "", line) for line in verified_source.splitlines())
            sections.append(f"--- File: {target_f} ---\n{denumbered[:12000]}")
        else:
            sections.append(f"--- File: {target_f} ---\n[Warning: Target file verified, read lines needed]")

        return "\n".join(sections)

    def compact_prompt(self, state: AgentState, trajectory: AgentTrajectory) -> str:
        """Assemble a clean, compact context prompt from state and recent trajectory."""
        if state.phase.value == "PATCH":
            return self._build_repair_context(state, trajectory)

        sections = []

        # 0. Active Agent Phase & Guidance
        sections.append(f"=== CURRENT AGENT PHASE: {state.phase.value} (Turn {state.turn_count}/{state.max_turns}) ===")
        if state.phase.value == "INVESTIGATE":
            sections.append("Objective: Locate the root cause file and symbol using search and read tools.")
        elif state.phase.value == "SPECIFY":
            sections.append("Objective: Review expected vs observed behavior and constraints.")
        elif state.phase.value == "HYPOTHESIZE":
            sections.append("Objective: Formulate your causal hypothesis explaining root cause and repair approach. You may read specific code lines, call formulate_hypothesis, or explain your hypothesis.")
        elif state.phase.value == "PLAN":
            sections.append("Objective: Prepare specific edits and target lines. You may read specific code lines, call propose_plan, or proceed directly to apply_patch.")
        elif state.phase.value == "TEST":
            sections.append("Objective: Validate candidate patch against test cases.")
        elif state.phase.value in ("DIAGNOSE", "REFINE"):
            sections.append("Objective: Execution failed. Refine your patch based on the failure traceback.")

        # 1. Problem Statement
        sections.append("\n=== PROBLEM STATEMENT ===")
        sections.append(state.problem.problem_statement.strip()[:2000])

        # 2. Program Understanding & Repair Specification
        if state.understanding:
            sections.append("\n=== CURRENT PROGRAM UNDERSTANDING ===")
            sections.append(state.understanding.format_summary())

        if state.specification:
            sections.append("\n=== REPAIR SPECIFICATION ===")
            sections.append(state.specification.format_summary())

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

        # 5. Active Hypothesis & Repair Plan
        if state.active_hypothesis:
            sections.append(f"\n=== ACTIVE HYPOTHESIS: {state.active_hypothesis.id} ===")
            sections.append(state.active_hypothesis.description)
            if state.active_hypothesis.expected_behavior:
                sections.append(f"Expected Behavior: {state.active_hypothesis.expected_behavior}")

        if state.active_plan:
            sections.append("\n=== ACTIVE REPAIR PLAN ===")
            sections.append(state.active_plan.format_summary())


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
            and (
                (isinstance(step.tool_result, dict) and step.tool_result.get("status") == "SUCCESS" and step.tool_result.get("data", {}).get("content"))
                or (hasattr(step.tool_result, "status") and step.tool_result.status == "SUCCESS" and hasattr(step.tool_result, "data") and step.tool_result.data.get("content"))
            )
        ][-self.MAX_RECENT_RAW_READS:]
        if recent_reads:
            sections.append("\n=== MOST RECENT FILE CONTENT (VERBATIM) ===")
            for step in recent_reads:
                res_data = step.tool_result.get("data", {}) if isinstance(step.tool_result, dict) else (step.tool_result.data if hasattr(step.tool_result, "data") else {})
                content = res_data.get("content", "")
                if len(content) > self.MAX_RAW_READ_CHARS:
                    truncated = content[:self.MAX_RAW_READ_CHARS]
                    last_nl = truncated.rfind("\n")
                    if last_nl > 0:
                        truncated = truncated[:last_nl]
                    content = truncated + "\n[... raw read content truncated at line-safe defensive limit ...]"
                file_path = res_data.get("file_path", "")
                sections.append(f"--- Turn {step.turn}, File: {file_path} ---\n{content}")


        # 8. Recent Action-Observation Memory (Last 3 steps only to retain narrative continuity)
        recent_steps = trajectory.steps[-3:]
        if recent_steps:
            sections.append("\n=== RECENT TOOL ACTIONS & OBSERVATIONS ===")
            for step in recent_steps:
                sections.append(f"[Turn {step.turn}] Action: {step.action_name} -> {step.observation_summary[:250]}")

        return "\n".join(sections)
