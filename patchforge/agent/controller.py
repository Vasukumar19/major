"""Bounded agentic controller for PatchForge v0.3."""
from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

from patchforge.agent.context import ContextCompactor
from patchforge.agent.policy import AgentPolicy
from patchforge.agent.state import AgentPhase, AgentState, ExecutionEvidence
from patchforge.agent.trajectory import AgentTrajectory
from patchforge.issue.problem import Problem
from patchforge.models.provider import ModelProvider
from patchforge.reasoning.hypothesis import Hypothesis
from patchforge.reasoning.plan import RepairPlan
from patchforge.reasoning.probes import ProbeResult, StaticProbe, TestProbe, TracebackProbe
from patchforge.reasoning.specification import RepairSpecification, derive_specification
from patchforge.reasoning.understanding import ProgramUnderstanding, derive_understanding
from patchforge.repair.generator import parse_edits
from patchforge.repair.patch import Patch
from patchforge.retrieval.evidence import Evidence, EvidenceType
from patchforge.tools.base import ToolCall, ToolRegistry, ToolResult
from patchforge.verification.classifier import classify, FailureClass


SYSTEM_CONTROLLER_PROMPT = """You are PatchForge v0.3, an autonomous debugging and software-repair agent.
You progress through explicit phases to inspect, diagnose, plan, patch, and test software bugs:
1. INVESTIGATE: Locate the root cause file and symbol using search and read tools.
2. SPECIFY: Define the bug fix requirements and non-regression constraints (tool: specify_repair or formulate in thought).
3. HYPOTHESIZE: Formulate a causal hypothesis explaining the root cause and proposed fix strategy (tool: formulate_hypothesis or formulate in thought).
4. PLAN: Prepare the specific code modification and target lines (tool: propose_plan or formulate in thought).
5. PATCH: Apply SEARCH/REPLACE edits using apply_patch. The SEARCH block must be an exact verbatim quote from code visible in the prompt.
6. TEST: Validate candidate patch against tests.
7. DIAGNOSE / RETRY: If test execution fails, inspect the traceback and refine the patch.

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
4. When ready to conclude the session after test validation, you can output:
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


SYSTEM_PATCH_PROMPT = """You are PatchForge v0.4, an autonomous software-repair agent.
You have access to the following tool:
[
  {
    "name": "apply_patch",
    "description": "Apply SEARCH/REPLACE block edits to a file.",
    "parameters": {
      "type": "object",
      "properties": {
        "patch_text": {
          "type": "string",
          "description": "SEARCH/REPLACE formatted text: ### <file>\\n<<<<<<< SEARCH\\n<exact code>\\n=======\\n<replacement>\\n>>>>>>> REPLACE"
        }
      },
      "required": ["patch_text"]
    }
  }
]

Output a single valid JSON object:
```json
{
  "thought": "Applying SEARCH/REPLACE fix to <file>",
  "action": {
    "name": "apply_patch",
    "arguments": {
      "patch_text": "### <file>\\n<<<<<<< SEARCH\\n...\\n=======\\n...\\n>>>>>>> REPLACE"
    }
  }
}
```
Only output the JSON object. Do not output conversational prose or explanations."""


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

        # Strip <think>...</think> tags if present (e.g. Qwen/DeepSeek reasoning output)
        think_text = ""
        think_match = re.search(r"<think>(.*?)</think>", clean_text, re.DOTALL)
        if think_match:
            think_text = think_match.group(1).strip()
            clean_text = clean_text.split("</think>")[-1].strip()
        elif "<think>" in clean_text:
            clean_text = clean_text.split("<think>")[-1].strip()

        # Check if model directly emitted SEARCH/REPLACE block(s)
        edits = parse_edits(clean_text)
        if edits:
            thought_match = re.search(r'"thought":\s*"([^"]+)"', clean_text)
            thought = thought_match.group(1) if thought_match else think_text or clean_text.split("###")[0].strip()
            return thought or "Applying SEARCH/REPLACE edit", ToolCall(name="apply_patch", arguments={"patch_text": clean_text})

        # Try to find JSON inside markdown code blocks first
        candidates = []
        json_blocks = re.findall(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", clean_text)
        if json_blocks:
            candidates.extend(json_blocks)

        # Try finding outer curly braces
        first_brace = clean_text.find("{")
        last_brace = clean_text.rfind("}")
        if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
            candidates.append(clean_text[first_brace:last_brace + 1])

        candidates.append(clean_text)

        for cand in candidates:
            try:
                data = json.loads(cand, strict=False)
                thought = data.get("thought", "")
                action = data.get("action", {})
                if isinstance(action, dict):
                    name = action.get("name", "") or action.get("tool", "")
                    args = action.get("arguments", {}) or action.get("args", {}) or action.get("parameters", {})
                elif isinstance(action, str):
                    name = action
                    args = data.get("arguments", {}) or data.get("args", {}) or data.get("parameters", {})
                else:
                    name = data.get("name", "") or data.get("tool", "") or data.get("tool_name", "")
                    args = data.get("arguments", {}) or data.get("args", {}) or data.get("parameters", {})

                if name:
                    if name == "apply_patch" and isinstance(args, dict) and not args.get("patch_text") and args.get("file_path") and (args.get("search") is not None or args.get("replace") is not None):
                        fp = args.get("file_path")
                        s = args.get("search", "")
                        r = args.get("replace", "")
                        args["patch_text"] = f"### {fp}\n<<<<<<< SEARCH\n{s}\n=======\n{r}\n>>>>>>> REPLACE"
                    return thought, ToolCall(name=name, arguments=args)
                if thought:
                    return thought, None
            except Exception:
                continue

        # Regex extraction fallback
        thought_match = re.search(r'"thought":\s*"([^"]+)"', clean_text, re.DOTALL)
        action_match = re.search(r'"name":\s*"([^"]+)"', clean_text)
        thought = thought_match.group(1) if thought_match else clean_text[:300]
        name = action_match.group(1) if action_match else ""
        if name:
            args_match = re.search(r'"arguments":\s*(\{[\s\S]*?\})', clean_text)
            args = {}
            if args_match:
                try:
                    args = json.loads(args_match.group(1), strict=False)
                except Exception:
                    pass
            return thought, ToolCall(name=name, arguments=args)

        return clean_text[:300], None

    def classify_patch_output(
        self,
        thought: str,
        tool_call: ToolCall | None,
        text: str,
        state: AgentState,
    ) -> str:
        """Classify model output specifically in the PATCH phase into deterministic categories."""
        if not tool_call or tool_call.name != "apply_patch":
            clean = (thought or text).strip()
            if "diff --git" in clean or "@@" in clean or "--- a/" in clean:
                return "WRONG_PATCH_FORMAT"
            return "PROSE_NO_TOOL"

        args = tool_call.arguments if isinstance(tool_call.arguments, dict) else {}
        patch_text = str(args.get("patch_text", "") or "")

        if not patch_text and args.get("file_path") and (args.get("search") is not None or args.get("replace") is not None):
            fp = args.get("file_path", "")
            s = args.get("search", "")
            r = args.get("replace", "")
            patch_text = f"### {fp}\n<<<<<<< SEARCH\n{s}\n=======\n{r}\n>>>>>>> REPLACE"
            tool_call.arguments["patch_text"] = patch_text

        if not patch_text:
            return "INVALID_TOOL_SCHEMA"

        if ("diff --git" in patch_text or "@@" in patch_text or "--- a/" in patch_text or "*** Begin" in patch_text) and ("<<<<<<< SEARCH" not in patch_text):
            return "WRONG_PATCH_FORMAT"

        edits = parse_edits(patch_text)
        if not edits:
            return "WRONG_PATCH_FORMAT"

        # Target path grounding normalization:
        expected_target = (state.active_plan.target_file if state.active_plan else "") or (state.verified_read_files[0] if state.verified_read_files else "")
        if expected_target and (Path(state.repo_dir) / expected_target).exists():
            for target_file in list(edits.keys()):
                if not (Path(state.repo_dir) / target_file).exists():
                    try:
                        with open(Path(state.repo_dir) / expected_target, "r", encoding="utf-8", errors="replace") as f:
                            exp_content = f.read()
                        blocks = edits[target_file]
                        if any(item[0].strip() in exp_content for item in blocks if (isinstance(item, tuple) and item[0].strip())):
                            patch_text = re.sub(r"^###\s+[^\r\n]+", f"### {expected_target}", patch_text, count=1, flags=re.MULTILINE)
                            tool_call.arguments["patch_text"] = patch_text
                            edits = parse_edits(patch_text)
                            break
                    except Exception:
                        pass

        for target_file, blocks in edits.items():
            if target_file not in state.verified_read_files and target_file not in state.pinned_snippets:
                return "UNVERIFIED_FILE"

            # Validate search block grounding against local file
            repo_path = Path(state.repo_dir) / target_file
            if repo_path.exists():
                try:
                    with open(repo_path, "r", encoding="utf-8", errors="replace") as f:
                        file_content = f.read()
                    for item in blocks:
                        s_block = item[0] if isinstance(item, tuple) else getattr(item, "search_block", "")
                        if s_block and s_block.strip() not in file_content:
                            return "SEARCH_NOT_FOUND"
                except Exception:
                    pass

        return "VALID_PATCH"

    def _classify_model_output(
        self,
        thought: str,
        tool_call: ToolCall | None,
        text: str,
        state: AgentState,
    ) -> str:
        """Classify model output into structured decision categories."""
        if state.phase == AgentPhase.PATCH:
            patch_cat = self.classify_patch_output(thought, tool_call, text, state)
            if patch_cat != "PROSE_NO_TOOL":
                return patch_cat

        if tool_call:
            if tool_call.name == "done":
                return "DONE"
            if tool_call.name == "formulate_hypothesis":
                return "VALID_HYPOTHESIS"
            if tool_call.name == "propose_plan":
                return "VALID_PLAN"
            if tool_call.name == "specify_repair":
                return "VALID_SPECIFICATION"
            if self.tools.get(tool_call.name) is not None:
                return "VALID_TOOL_CALL"
            return "MALFORMED_ACTION"

        clean_thought = (thought or text).lower()
        if not clean_thought.strip():
            return "NO_ACTION"

        if "done" in clean_thought and ("finish" in clean_thought or "conclude" in clean_thought):
            return "DONE"

        if state.phase in (AgentPhase.HYPOTHESIZE, AgentPhase.SPECIFY) or "hypothesis" in clean_thought or "root cause" in clean_thought:
            return "VALID_HYPOTHESIS"
        if state.phase == AgentPhase.PLAN or "plan" in clean_thought or "patch" in clean_thought:
            return "VALID_PLAN"

        return "REASONING_ONLY"

    def acquire_target_context(self, state: AgentState, trajectory: AgentTrajectory) -> bool:
        """Deterministically acquire exact source context for the planned target file before patch synthesis."""
        target_f = (state.active_plan.target_file if state.active_plan else "") or state.last_failed_patch_file
        if not target_f and state.active_hypothesis and state.active_hypothesis.affected_files:
            target_f = state.active_hypothesis.affected_files[0]
        if not target_f and state.visited_files:
            target_f = state.visited_files[0]
        if not target_f and state.understanding and state.understanding.key_files:
            target_f = state.understanding.key_files[0]

        # Resolve target_f if it does not exist directly in repo_dir
        repo_p = Path(state.repo_dir)
        if target_f and not (repo_p / target_f).exists():
            resolved = None
            fname = Path(target_f).name
            for vf in state.visited_files:
                if vf.endswith(target_f) or Path(vf).name == fname:
                    resolved = vf
                    break
            if not resolved:
                candidates = [
                    str(p.relative_to(repo_p)).replace("\\", "/")
                    for p in repo_p.glob(f"**/{fname}")
                    if p.is_file() and not any(part in ("tests", "docs", ".git", "experiments", "venv", ".venv") for part in p.parts)
                ]
                if candidates:
                    resolved = candidates[0]
            if resolved:
                if state.active_plan:
                    state.active_plan.target_file = resolved
                target_f = resolved

        if not target_f or target_f in state.invalid_paths:
            return False

        if target_f in state.verified_read_files:
            return True

        # Calculate line range centered on target symbol if found
        start_line = 1
        end_line = 120
        full_p = repo_p / target_f
        if full_p.exists():
            try:
                with open(full_p, "r", encoding="utf-8", errors="replace") as f:
                    file_lines = f.readlines()
                sym = (state.active_plan.target_symbols[0] if (state.active_plan and state.active_plan.target_symbols) else "")
                if sym:
                    clean_sym = sym.split(".")[-1]
                    for idx, line in enumerate(file_lines, 1):
                        if f"def {clean_sym}" in line or f"class {clean_sym}" in line:
                            start_line = max(1, idx - 10)
                            end_line = min(len(file_lines), idx + 80)
                            break
            except Exception:
                pass

        # Use existing read_file tool
        read_call = ToolCall(name="read_file", arguments={"file_path": target_f, "start_line": start_line, "end_line": end_line})
        read_result = self.tools.execute(read_call, state=state)

        if read_result.status == "SUCCESS" and read_result.data and read_result.data.get("content"):
            snippet = read_result.data.get("content", "")
            state.record_file_visit(target_f, snippet)
            if target_f not in state.verified_read_files:
                state.verified_read_files.append(target_f)
            if state.last_failed_patch_file == target_f:
                state.last_failed_patch_file = ""
            trajectory.record_step(
                turn=state.turn_count,
                phase=state.phase.value,
                thought=f"Deterministic context acquisition: verified source context for '{target_f}' before patch synthesis.",
                tool_call=read_call,
                tool_result=read_result,
            )
            return True
        else:
            err_str = str(read_result.error or read_result.message or "")
            if "does not exist" in err_str or "not found" in err_str:
                if target_f not in state.invalid_paths:
                    state.invalid_paths.append(target_f)
            trajectory.record_step(
                turn=state.turn_count,
                phase=state.phase.value,
                thought=f"Deterministic context acquisition failed for '{target_f}': {err_str}",
                tool_call=read_call,
                tool_result=read_result,
            )
            return False

    def run(
        self,
        problem: Problem,
        instance_id: str = "",
        repo_dir: str = ".",
        tester: Any = None,
        repograph_adapter: Any = None,
        initial_state: AgentState | None = None,
    ) -> tuple[Patch | None, AgentTrajectory, AgentState]:
        """Execute the full agentic repair session."""
        state = initial_state or AgentState(
            instance_id=instance_id or problem.instance_id,
            problem=problem,
            repo_dir=repo_dir,
            tester=tester,
            repograph_adapter=repograph_adapter,
            max_turns=self.policy.max_turns,
        )
        trajectory = AgentTrajectory(instance_id=state.instance_id)

        # 1. Initial Program Understanding & Specification
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

        try:
            state.specification = derive_specification(problem, state.understanding)
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
            state.record_phase_turn(state.phase)
            if state.phase == AgentPhase.INVESTIGATE:
                state.localization_turn_count += 1
                # Bounded investigation: advance after 2-3 turns once candidates are identified
                if state.localization_turn_count >= 2 and (state.visited_files or state.pinned_snippets):
                    state.phase = AgentPhase.HYPOTHESIZE

            elif state.phase == AgentPhase.PLAN:
                # Bounded plan: advance immediately to PATCH to prevent search drift
                if state.active_plan or state.phase_turn_counts.get("PLAN", 0) >= 1:
                    state.phase = AgentPhase.PATCH

            # Deterministic Target Context Acquisition: Ensure target source is verified before PATCH prompt generation
            if state.phase == AgentPhase.PATCH:
                self.acquire_target_context(state, trajectory)

            # 2. Context Compaction & Prompt Generation
            context_prompt = self.compactor.compact_prompt(state, trajectory)

            # 3. Model Action Selection with phase-specific system prompt
            curr_sys_prompt = SYSTEM_PATCH_PROMPT if state.phase == AgentPhase.PATCH else system_prompt
            try:
                resp = self.provider.generate_one(context_prompt, system=curr_sys_prompt)
            except Exception as e:
                print(f"[{state.instance_id} Turn {state.turn_count}] Model generation failed ({e}). Retrying once...", flush=True)
                try:
                    resp = self.provider.generate_one(context_prompt, system=system_prompt)
                except Exception as e2:
                    print(f"[{state.instance_id} Turn {state.turn_count}] Retry also failed ({e2}). Continuing with reasoning fallback.", flush=True)
                    resp = type("DummyResp", (), {
                        "text": '{"thought": "Model call timed out, retrying next action"}',
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "cost_usd": 0.0,
                    })()
            state.input_tokens += resp.input_tokens
            state.output_tokens += resp.output_tokens
            state.total_cost_usd += resp.cost_usd

            thought, tool_call = self._parse_model_action(resp.text)
            classification = self._classify_model_output(thought, tool_call, resp.text, state)
            action_disp = tool_call.name if tool_call else "None"
            print(f"[{state.instance_id} Turn {state.turn_count} ({state.phase.value})] action={action_disp}, thought={thought[:80]!r}".encode("ascii", "replace").decode("ascii"), flush=True)

            # 4. Handle Case When Model Produces No Tool Call (Reasoning / Natural Progression)
            if not tool_call:
                if classification == "DONE":
                    state.phase = AgentPhase.DONE
                    trajectory.record_step(
                        turn=state.turn_count,
                        phase=state.phase.value,
                        thought=thought,
                        tool_call=None,
                        tool_result=ToolResult("done", "SUCCESS", message="Agent completed session."),
                    )
                    trajectory.finish(verdict="Session ended by agent.", resolved=bool(state.best_patch and state.best_score >= 1.0))
                    break

                # Advance Phase State Machine Logically Based on Reasoning
                if state.phase == AgentPhase.INVESTIGATE:
                    if state.pinned_snippets or state.visited_files or state.distinct_evidence_count() >= 1:
                        state.phase = AgentPhase.SPECIFY if not state.specification else AgentPhase.HYPOTHESIZE
                elif state.phase == AgentPhase.SPECIFY:
                    state.phase = AgentPhase.HYPOTHESIZE
                elif state.phase == AgentPhase.HYPOTHESIZE:
                    hyp_id = f"H{len(state.hypotheses) + 1}"
                    affected = list(state.visited_files.keys()) if isinstance(state.visited_files, dict) else list(state.visited_files)[:2]
                    hyp = Hypothesis(
                        id=hyp_id,
                        description=thought[:300] if thought else (state.understanding.summary if state.understanding else "Root cause"),
                        confidence=0.8,
                        affected_files=affected,
                    )
                    state.hypotheses.append(hyp)
                    state.active_hypothesis = hyp
                    state.phase = AgentPhase.PLAN
                elif state.phase == AgentPhase.PLAN:
                    target_files = list(state.visited_files)[:2] or (state.understanding.key_files if state.understanding else [])
                    plan = RepairPlan(
                        target_file=target_files[0] if target_files else "",
                        target_symbols=list(state.visited_symbols)[:3] or (state.understanding.key_symbols if state.understanding else []),
                        intended_change=thought[:300] if thought else "Targeted code fix",
                        rationale=thought[:200] if thought else "Repair plan derived from hypothesis",
                    )
                    state.active_plan = plan
                    state.phase = AgentPhase.PATCH
                    self.acquire_target_context(state, trajectory)
                elif state.phase == AgentPhase.PATCH:
                    target_f = (state.active_plan.target_file if state.active_plan else "") or state.last_failed_patch_file
                    if not target_f and state.visited_files:
                        target_f = state.visited_files[0]
                    if not target_f and state.understanding and state.understanding.key_files:
                        target_f = state.understanding.key_files[0]

                    raw_candidate = (thought or resp.text).strip()
                    if "<<<<<<< SEARCH" in raw_candidate and "### " not in raw_candidate and target_f:
                        raw_candidate = f"### {target_f}\n" + raw_candidate

                    edits = parse_edits(raw_candidate)
                    if edits:
                        tool_call = ToolCall(name="apply_patch", arguments={"patch_text": raw_candidate})
                    else:
                        # If model repeatedly outputs prose without tool call, prompt model directly for SEARCH/REPLACE block
                        prose_streak = state.guard_firings.get("prose_no_tool_blocks", 0)
                        if prose_streak >= 1 and target_f:
                            direct_src = state.pinned_snippets.get(target_f, "")
                            if not direct_src and state.verified_read_files:
                                direct_src = state.pinned_snippets.get(state.verified_read_files[0], "")
                            direct_prompt = (
                                f"=== PATCH SYNTHESIS DIRECTIVE ===\n"
                                f"Issue: {state.problem.problem_statement.strip()[:800]}\n"
                                f"Target File: {target_f}\n"
                                f"Plan: {state.active_plan.format_summary() if state.active_plan else 'Fix bug'}\n\n"
                                f"VERIFIED SOURCE:\n{direct_src[:6000]}\n\n"
                                f"Generate the fix. Reply ONLY with the SEARCH/REPLACE block:\n"
                                f"### {target_f}\n<<<<<<< SEARCH\n<exact lines>\n=======\n<replacement>\n>>>>>>> REPLACE"
                            )
                            try:
                                d_resp = self.provider.generate_one(direct_prompt, system=SYSTEM_PATCH_PROMPT)
                                d_text = d_resp.text.strip()
                                if "<<<<<<< SEARCH" in d_text and "### " not in d_text:
                                    d_text = f"### {target_f}\n" + d_text
                                d_edits = parse_edits(d_text)
                                if d_edits:
                                    tool_call = ToolCall(name="apply_patch", arguments={"patch_text": d_text})
                            except Exception:
                                pass

                    if not tool_call:
                        # Check if unified diff format was produced
                        if "diff --git" in (thought or resp.text) or "@@" in (thought or resp.text):
                            state.guard_firings["wrong_patch_format_blocks"] = state.guard_firings.get("wrong_patch_format_blocks", 0) + 1
                            tool_result = ToolResult(
                                "apply_patch",
                                "BLOCKED",
                                message=(
                                    f"WRONG_PATCH_FORMAT: You emitted unified diff syntax ('diff --git', '@@'). "
                                    f"You MUST use SEARCH/REPLACE block syntax targeting '{target_f}':\n"
                                    f"### {target_f}\n<<<<<<< SEARCH\n<exact lines from file>\n=======\n<replacement lines>\n>>>>>>> REPLACE"
                                ),
                            )
                        else:
                            state.guard_firings["prose_no_tool_blocks"] = state.guard_firings.get("prose_no_tool_blocks", 0) + 1
                            state.guard_firings["passive_patch_recovery"] = state.guard_firings.get("passive_patch_recovery", 0) + 1
                            tool_result = ToolResult(
                                "apply_patch",
                                "BLOCKED",
                                message=(
                                    f"PROSE_NO_TOOL: You emitted conversational prose instead of an apply_patch action. "
                                    f"Do NOT explain the patch. Your next action MUST be a single JSON tool call with name='apply_patch' "
                                    f"and arguments.patch_text using SEARCH/REPLACE format targeting '{target_f}'."
                                ),
                            )
                        trajectory.record_step(
                            turn=state.turn_count,
                            phase=state.phase.value,
                            thought=thought,
                            tool_call=None,
                            tool_result=tool_result,
                        )
                        continue
                elif state.phase == AgentPhase.TEST:
                    if state.last_execution_evidence and not state.last_execution_evidence.exit_code == 0:
                        state.phase = AgentPhase.DIAGNOSE
                    else:
                        state.phase = AgentPhase.DONE
                elif state.phase == AgentPhase.DIAGNOSE:
                    state.phase = AgentPhase.PLAN

                if not tool_call:
                    # Record reasoning step when no tool action was synthesized
                    trajectory.record_step(
                        turn=state.turn_count,
                        phase=state.phase.value,
                        thought=thought,
                        tool_call=None,
                        tool_result=ToolResult("reasoning", "SUCCESS", message=thought[:300] if thought else "Reasoning step recorded."),
                    )
                    continue

            # Check if agent indicated completion explicitly
            if tool_call.name == "done":
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

            # 5. Evaluate Action Guards (Repetition, Information-Gain, Phase Transition)
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

            # 6. Execute Selected Tool
            tool_result = self.tools.execute(tool_call, state=state)
            state.recent_tool_calls.append({
                "name": tool_call.name,
                "arguments": getattr(tool_call, "arguments", {}) or {},
            })

            # 7. Information-Gain & State Updates Based on Tool
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
                # Intervention D: Path recovery from search matches
                if state.active_plan:
                    for m in matches:
                        df = m.get("file", "")
                        if not state.active_plan.target_file or state.active_plan.target_file in state.invalid_paths:
                            state.active_plan.target_file = df
                            break
                        elif state.active_plan.target_file and df.endswith(state.active_plan.target_file) and df != state.active_plan.target_file:
                            state.active_plan.target_file = df
                            break

            elif tool_call.name in ("find_symbol", "find_references") and tool_result.status == "SUCCESS":
                syms = tool_result.data.get("symbols", []) or tool_result.data.get("references", [])
                for s in syms:
                    sym_name = s.get("symbol") or s.get("name")
                    if sym_name and sym_name not in state.visited_symbols:
                        info_gained = True
                        state.visited_symbols.append(sym_name)
                    df = s.get("file") or ""
                    if df:
                        if df not in state.visited_files:
                            state.visited_files.append(df)
                        # Intervention D: Path recovery from symbol lookup
                        if state.active_plan:
                            if not state.active_plan.target_file or state.active_plan.target_file in state.invalid_paths:
                                state.active_plan.target_file = df
                            elif state.active_plan.target_file and df.endswith(state.active_plan.target_file) and df != state.active_plan.target_file:
                                state.active_plan.target_file = df

            elif tool_call.name == "read_file":
                fp = tool_result.data.get("file_path", "") or tool_call.arguments.get("file_path", "")
                if tool_result.status == "SUCCESS":
                    snippet = tool_result.data.get("content", "")
                    if fp not in state.visited_files:
                        info_gained = True
                    state.record_file_visit(fp, snippet)
                    if fp not in state.verified_read_files:
                        state.verified_read_files.append(fp)
                    if state.last_failed_patch_file == fp:
                        state.last_failed_patch_file = ""
                    if fp in state.invalid_paths:
                        state.invalid_paths.remove(fp)
                else:
                    # Intervention D: Path error recovery - invalidate non-existent file
                    err_str = str(tool_result.error or tool_result.message or "")
                    if fp and ("does not exist" in err_str or "not found" in err_str or "No such file" in err_str):
                        if fp not in state.invalid_paths:
                            state.invalid_paths.append(fp)
                        if fp in state.visited_files:
                            state.visited_files.remove(fp)
                        if fp in state.pinned_snippets:
                            del state.pinned_snippets[fp]
                        if fp in state.verified_read_files:
                            state.verified_read_files.remove(fp)
                        if state.active_plan and state.active_plan.target_file == fp:
                            state.active_plan.target_file = ""

            elif tool_call.name == "formulate_hypothesis" and tool_result.status == "SUCCESS":
                info_gained = True
                state.phase = AgentPhase.PLAN

            elif tool_call.name == "propose_plan" and tool_result.status == "SUCCESS":
                info_gained = True
                state.phase = AgentPhase.PATCH
                self.acquire_target_context(state, trajectory)

            elif tool_call.name == "specify_repair" and tool_result.status == "SUCCESS":
                info_gained = True
                state.phase = AgentPhase.HYPOTHESIZE

            elif tool_call.name in ("apply_patch", "run_targeted_test", "inspect_failure"):
                info_gained = True

            if state.phase == AgentPhase.INVESTIGATE:
                if info_gained:
                    state.zero_info_turn_streak = 0
                else:
                    state.zero_info_turn_streak += 1

            # 8. Handle Patch Application & Validation
            if tool_call.name == "apply_patch":
                if tool_result.status != "SUCCESS":
                    # Intervention C: Record patch failure and require verified read before retry
                    err_msg = str(tool_result.error or tool_result.message or "")
                    patch_text = str(tool_call.arguments.get("patch_text", "") or "")
                    target_f = ""
                    m = re.search(r"^###\s+([^\r\n]+)", patch_text, re.MULTILINE)
                    if m:
                        target_f = m.group(1).strip()
                    if not target_f and state.active_plan:
                        target_f = state.active_plan.target_file

                    if target_f:
                        state.last_failed_patch_file = target_f
                        state.add_evidence(Evidence(
                            source="patch_error",
                            type=EvidenceType.TRACEBACK.value,
                            file=target_f,
                            explanation=f"apply_patch failed: {err_msg[:200]}. Must call read_file on '{target_f}' before retrying.",
                        ))
                else:
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
                    if not state.active_plan:
                        files_changed = patch_data.get("files_changed", [])
                        state.active_plan = RepairPlan(
                            target_file=files_changed[0] if files_changed else "",
                            intended_change=thought[:300] if thought else "Applied patch",
                            rationale=thought[:200] if thought else "Direct patch execution",
                        )
                    state.phase = AgentPhase.TEST

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
                            trajectory.record_step(
                                turn=state.turn_count,
                                phase=state.phase.value,
                                thought=thought,
                                tool_call=tool_call,
                                tool_result=tool_result,
                            )
                            trajectory.finish(verdict=policy_res.reason, resolved=passed)
                            break
                        state.phase = policy_res.next_phase or (AgentPhase.DONE if passed else AgentPhase.DIAGNOSE)
                    else:
                        state.best_patch = patch_obj
                        state.best_score = 1.0
                        state.phase = AgentPhase.DONE
                        trajectory.record_step(
                            turn=state.turn_count,
                            phase=state.phase.value,
                            thought=thought,
                            tool_call=tool_call,
                            tool_result=tool_result,
                        )
                        trajectory.finish(verdict="Patch applied successfully (no tester).", resolved=True)
                        break
            elif tool_call.name == "inspect_failure" and tool_result.status == "SUCCESS":
                tb = tool_result.data.get("traceback_snippet", "")
                ft = tool_result.data.get("failing_tests", [])
                if state.last_execution_evidence:
                    state.last_execution_evidence.traceback = tb
                    state.last_execution_evidence.failing_tests = ft
                state.phase = AgentPhase.DIAGNOSE

            # 9. Record Step in Trajectory
            trajectory.record_step(
                turn=state.turn_count,
                phase=state.phase.value,
                thought=thought,
                tool_call=tool_call,
                tool_result=tool_result,
            )

        return state.best_patch or state.active_patch, trajectory, state
