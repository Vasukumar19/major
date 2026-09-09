"""Bounded agentic controller for PatchForge v0.3."""
from __future__ import annotations

import json
import re
from typing import Any

from patchforge.agent.context import ContextCompactor
from patchforge.agent.policy import AgentPolicy
from patchforge.agent.state import AgentPhase, AgentState, ExecutionEvidence
from patchforge.agent.trajectory import AgentTrajectory
from patchforge.issue.problem import Problem
from patchforge.models.provider import ModelProvider
from patchforge.reasoning.hypothesis import Hypothesis
from patchforge.reasoning.probes import ProbeResult, StaticProbe, TestProbe, TracebackProbe
from patchforge.reasoning.understanding import ProgramUnderstanding, derive_understanding
from patchforge.repair.patch import Patch
from patchforge.retrieval.evidence import Evidence, EvidenceType
from patchforge.tools.base import ToolCall, ToolRegistry, ToolResult
from patchforge.verification.classifier import classify, FailureClass


SYSTEM_CONTROLLER_PROMPT = """You are PatchForge v0.3, an autonomous debugging and software-repair agent.
You investigate issues, inspect code and tests, query code graphs, form and probe hypotheses, apply patches, and inspect test failures to iteratively refine fixes.

You have access to the following tools:
{tool_schemas}

Instructions:
1. Always analyze the current context, evidence, and failure traces carefully.
2. For apply_patch, the SEARCH section must be an exact, verbatim quote from code shown in the most recently read file content in the current prompt. Never paraphrase it, use placeholders, or rely on remembered text.
3. Output a single valid JSON object containing your reasoning and your NEXT tool action:
```json
{{
  "thought": "Detailed reasoning about current findings and what to do next",
  "action": {{
    "name": "tool_name",
    "arguments": {{ ... }}
  }}
}}
```
4. To finish after verifying a patch, or if giving up, you can call "apply_patch" or output:
```json
{{
  "thought": "Concluding repair session",
  "action": {{
    "name": "done",
    "arguments": {{}}
  }}
}}
```
Only output the JSON object."""


class AgentController:
    """Orchestrates autonomous debugging sessions with model-driven tool actions."""

    def __init__(
        self,
        provider: ModelProvider,
        tools: ToolRegistry,
        policy: AgentPolicy | None = None,
        compactor: ContextCompactor | None = None,
        model: str = "",
    ):
        self.provider = provider
        self.tools = tools
        self.policy = policy or AgentPolicy()
        self.compactor = compactor or ContextCompactor()
        self.model = model

    def _parse_model_action(self, text: str) -> tuple[str, ToolCall | None]:
        """Extract thought and ToolCall from LLM JSON response."""
        clean_text = text.strip()
        if clean_text.startswith("```"):
            lines = clean_text.splitlines()
            clean_text = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])

        try:
            data = json.loads(clean_text)
            thought = data.get("thought", "")
            action = data.get("action", {})
            name = action.get("name", "")
            args = action.get("arguments", {})
            if name:
                return thought, ToolCall(name=name, arguments=args)
            return thought, None
        except Exception:
            # Regex extraction fallback
            thought_match = re.search(r'"thought":\s*"([^"]+)"', clean_text)
            action_match = re.search(r'"name":\s*"([^"]+)"', clean_text)
            thought = thought_match.group(1) if thought_match else clean_text[:200]
            name = action_match.group(1) if action_match else ""
            if name:
                return thought, ToolCall(name=name, arguments={})
            return thought, None

    def run(
        self,
        problem: Problem,
        instance_id: str = "",
        repo_dir: str = ".",
        tester: Any = None,
        repograph_adapter: Any = None,
    ) -> tuple[Patch | None, AgentTrajectory, AgentState]:
        """Execute the full agentic repair session."""
        state = AgentState(
            instance_id=instance_id or problem.instance_id,
            problem=problem,
            repo_dir=repo_dir,
            tester=tester,
            repograph_adapter=repograph_adapter,
            max_turns=self.policy.max_turns,
        )
        trajectory = AgentTrajectory(instance_id=state.instance_id)

        # Initial Program Understanding
        try:
            state.understanding = derive_understanding(problem, self.provider, model=self.model)
            if state.understanding.key_files:
                for kf in state.understanding.key_files:
                    state.add_evidence(Evidence(
                        source="understanding",
                        type=EvidenceType.STATIC_REFERENCE.value,
                        file=kf,
                        relevance=0.8,
                        explanation=f"Identified as key file for {state.understanding.summary}",
                    ))
        except Exception:
            pass

        tool_schemas_text = json.dumps(self.tools.schemas(), indent=2)
        system_prompt = SYSTEM_CONTROLLER_PROMPT.format(tool_schemas=tool_schemas_text)

        while True:
            # 1. Evaluate Budget and Policy Limits
            budget_decision = self.policy.evaluate_budget(state)
            if budget_decision.should_terminate:
                state.phase = budget_decision.next_phase or AgentPhase.FAILED
                trajectory.finish(verdict=budget_decision.reason, resolved=False)
                break

            state.turn_count += 1
            if state.phase == AgentPhase.INVESTIGATE:
                state.localization_turn_count += 1

            # 2. Context Compaction & Prompt Generation
            context_prompt = self.compactor.compact_prompt(state, trajectory)

            # 3. Model Action Selection
            resp = self.provider.generate_one(context_prompt, system=system_prompt)
            state.input_tokens += resp.input_tokens
            state.output_tokens += resp.output_tokens
            state.total_cost_usd += resp.cost_usd

            thought, tool_call = self._parse_model_action(resp.text)

            # Check if agent indicated completion
            if not tool_call or tool_call.name == "done":
                state.phase = AgentPhase.DONE
                trajectory.record_step(
                    turn=state.turn_count,
                    phase=state.phase.value,
                    thought=thought,
                    tool_call=tool_call,
                    tool_result=ToolResult("done", "SUCCESS", message="Agent completed session."),
                )
                trajectory.finish(verdict="Session ended by agent.", resolved=bool(state.best_patch and state.best_score >= 1.0))
                break

            # 4. Evaluate Action Guards (Repetition, Information-Gain, Phase Transition)
            action_decision = self.policy.evaluate_action(state, tool_call)
            if action_decision.next_phase:
                state.phase = action_decision.next_phase

            if not action_decision.allowed:
                tool_result = ToolResult(
                    tool_name=tool_call.name,
                    status="BLOCKED",
                    message=action_decision.blocked_reason,
                    error=action_decision.blocked_reason,
                )
                state.recent_tool_calls.append({
                    "name": tool_call.name,
                    "arguments": getattr(tool_call, "arguments", {}) or {},
                })
                trajectory.record_step(
                    turn=state.turn_count,
                    phase=state.phase.value,
                    thought=thought,
                    tool_call=tool_call,
                    tool_result=tool_result,
                )
                continue

            # 5. Execute Selected Tool
            tool_result = self.tools.execute(tool_call, state=state)
            state.recent_tool_calls.append({
                "name": tool_call.name,
                "arguments": getattr(tool_call, "arguments", {}) or {},
            })

            # 6. Information-Gain & State Updates Based on Tool
            info_gained = False
            if tool_call.name in ("search_code", "search_exact") and tool_result.status == "SUCCESS":
                matches = tool_result.data.get("matches", [])
                for m in matches[:5]:
                    if m.get("file") not in state.visited_files:
                        info_gained = True
                    state.record_file_visit(m["file"], m.get("snippet", ""))
                    state.add_evidence(Evidence(
                        source="search",
                        type=EvidenceType.IDENTIFIER_MATCH.value,
                        file=m["file"],
                        relevance=0.7,
                        explanation=f"Found match: {m.get('snippet', '')}",
                    ))

            elif tool_call.name in ("find_symbol", "find_references") and tool_result.status == "SUCCESS":
                syms = tool_result.data.get("symbols", []) or tool_result.data.get("references", [])
                for s in syms:
                    sym_name = s.get("symbol") or s.get("name")
                    if sym_name and sym_name not in state.visited_symbols:
                        info_gained = True
                        state.visited_symbols.append(sym_name)

            elif tool_call.name == "read_file" and tool_result.status == "SUCCESS":
                fp = tool_result.data.get("file_path", "")
                snippet = tool_result.data.get("content", "")
                if fp not in state.visited_files:
                    info_gained = True
                state.record_file_visit(fp, snippet)

            elif tool_call.name in ("apply_patch", "run_targeted_test", "inspect_failure"):
                info_gained = True

            if state.phase == AgentPhase.INVESTIGATE:
                if info_gained:
                    state.zero_info_turn_streak = 0
                else:
                    state.zero_info_turn_streak += 1

            if tool_call.name == "apply_patch" and tool_result.status == "SUCCESS":
                patch_data = tool_result.data.get("patch")
                if patch_data:
                    patch_obj = Patch(
                        hypothesis_id=patch_data.get("hypothesis_id", "H1"),
                        files_changed=patch_data.get("files_changed", []),
                        patch_text=tool_result.data.get("diff", ""),
                        valid=True,
                        match_tier=patch_data.get("match_tier", "EXACT"),
                    )
                    state.active_patch = patch_obj
                    state.patch_attempts.append(patch_obj)
                    state.phase = AgentPhase.PATCH

                    # If tester is available, run container evaluation
                    if state.tester and state.instance_id:
                        verdict = state.tester.run(state.instance_id, patch_obj.patch_text)
                        state.last_test_output = getattr(verdict, "test_output", "")
                        
                        f2p_p = getattr(verdict, "fail_to_pass_passed", 0)
                        f2p_t = getattr(verdict, "fail_to_pass_total", 0)
                        p2p_r = getattr(verdict, "pass_to_pass_regressions", 0)
                        passed = bool(getattr(verdict, "resolved", False))
                        
                        score = (f2p_p / f2p_t) if f2p_t > 0 else (1.0 if passed else 0.0)
                        if score > state.best_score:
                            state.best_score = score
                            state.best_patch = patch_obj

                        state.last_execution_evidence = ExecutionEvidence(
                            failing_tests=getattr(verdict, "failed_tests", []),
                            traceback=state.last_test_output[:1000],
                            exit_code=0 if passed else 1,
                            fail_to_pass_passed=f2p_p,
                            fail_to_pass_total=f2p_t,
                            pass_to_pass_regressions=p2p_r,
                        )

                        policy_res = self.policy.evaluate_execution_result(state, passed, f2p_p, f2p_t, p2p_r)
                        if policy_res.should_terminate:
                            state.phase = policy_res.next_phase or AgentPhase.DONE
                            trajectory.finish(verdict=policy_res.reason, resolved=passed)
                            break
                        state.phase = policy_res.next_phase or AgentPhase.INVESTIGATE
            elif tool_call.name == "inspect_failure" and tool_result.status == "SUCCESS":
                tb = tool_result.data.get("traceback_snippet", "")
                ft = tool_result.data.get("failing_tests", [])
                if state.last_execution_evidence:
                    state.last_execution_evidence.traceback = tb
                    state.last_execution_evidence.failing_tests = ft

            # 7. Record Step in Trajectory
            trajectory.record_step(
                turn=state.turn_count,
                phase=state.phase.value,
                thought=thought,
                tool_call=tool_call,
                tool_result=tool_result,
            )

        return state.best_patch or state.active_patch, trajectory, state
