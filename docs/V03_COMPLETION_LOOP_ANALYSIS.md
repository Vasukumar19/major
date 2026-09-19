# V0.3 Completion Loop Analysis & Diagnostic Trace

## 1. Executive Summary

In the previous phase, context plumbing was fixed: raw `read_file` content is preserved verbatim up to 24,000 characters, pinned snippets are updated on re-reads, and `ApplyPatchTool` schemas/prompts eliminate `...` placeholders.

All 36 focused unit tests pass, and benchmark instances (`psf__requests-863`, `psf__requests-2674`, `psf__requests-1963`, `pallets__flask-4045`) successfully reach `HYPOTHESIZE` with 0 placeholder guard blocks.

However, all four benchmark runs terminate as `INCOMPLETE` with **0 patch calls**.

This document traces the exact execution trajectory of `psf__requests-863` and details why the agent gets halted in `HYPOTHESIZE` without ever advancing to `PATCH`.

---

## 2. Detailed Trace of `psf__requests-863`

Below is the turn-by-turn trace from `experiments/phase_d_v03/psf__requests-863/trajectory.json`:

```
Turn 1 [INVESTIGATE -> HYPOTHESIZE]:
  Model thought: "I need to locate the Request.__init__ method in requests/models.py to examine how hooks are processed."
  Model action: find_symbol("Request.__init__", "requests/models.py")
  Policy evaluation: Confidence threshold reached (confidence=0.85 >= 0.70 with 2 distinct evidence items).
  Tool result: BLOCKED (Confidence Transition). State transitions to HYPOTHESIZE.

Turn 2 [HYPOTHESIZE]:
  Model thought: "The issue stems from Request.__init__ wrapping list-valued hooks in additional lists. I need to locate the exact code in requests/models.py where this transformation occurs to formulate a patch hypothesis."
  Model action: read_file("requests/models.py", start_line=1, end_line=150)
  Tool result: SUCCESS. Returned lines 1-150 containing class Request and def __init__ hook initialization (lines 143-150).
  ContextCompactor: Verbatim 150 lines preserved in prompt for Turn 3 under === MOST RECENT FILE CONTENT (VERBATIM) ===.

Turn 3 [HYPOTHESIZE -> DONE (Premature Exit)]:
  Model generation: The model receives the prompt with the exact verbatim code. It generates reasoning in thought/prose describing the hook fix without generating a tool action (or emitting tool action as null/empty).
  Model action parser: `_parse_model_action()` returns (thought, None).
  Controller logic (lines 182-192):
    `if not tool_call or tool_call.name == "done":`
      `state.phase = AgentPhase.DONE`
      `trajectory.finish(verdict="Session ended by agent.", resolved=False)`
      `break`
  Result: Controller terminates the session immediately.
  Final Outcome: Status = INCOMPLETE, Patch Attempts = 0, Best Score = 0.0.
```

---

## 3. Current State Machine & Transition Conditions

### State Enum (`AgentPhase`)
```
INVESTIGATE -> UNDERSTAND -> HYPOTHESIZE -> VALIDATE -> PATCH -> EXECUTE -> REFINE -> DONE / FAILED
```

### Current Transition Points in Code:
1. **`INVESTIGATE -> HYPOTHESIZE`**:
   - Handled in `AgentPolicy.evaluate_action()`:
     - **Confidence Threshold**: `confidence >= 0.70` AND `distinct_evidence >= 2`.
     - **Circuit Breaker**: `localization_turn_count >= max_localization_turns` (default 4).
2. **`HYPOTHESIZE -> ?`**:
   - **No explicit transition logic exists** in `AgentPolicy` or `AgentController` to advance from `HYPOTHESIZE` to `PLAN` or `PATCH`.
   - The model is expected to autonomously formulate a plan and invoke `apply_patch`.
3. **What happens after `read_file`**:
   - File content is stored in `state.visited_files` and `state.pinned_snippets`.
   - Preserved verbatim in `ContextCompactor`.
   - `state.phase` remains `HYPOTHESIZE`.
4. **What happens on reasoning-only model output**:
   - `_parse_model_action()` returns `thought, None`.
   - `AgentController.run()` checks `if not tool_call:` and immediately marks `state.phase = AgentPhase.DONE` and exits the loop.
5. **Where the controller decides whether another turn is allowed**:
   - `AgentPolicy.evaluate_budget()` checks max turns, cost budget, and max patch attempts.
   - But if `tool_call is None`, `AgentController.run()` exits *before* consulting budget policy for continuation.

---

## 4. Root Cause Analysis of `INCOMPLETE` Status

The benchmark failure is caused by three distinct control-flow and phase progression gaps:

1. **Premature Termination on Missing Tool Action**:
   When a model outputs natural reasoning, hypothesis analysis, or malformed JSON without an explicit tool call name, `_parse_model_action` returns `tool_call = None`. The controller unconditionally interprets `not tool_call` as `done` and terminates the entire session instead of classifying the output, steering the agent, or advancing the phase.

2. **Missing Phase Progression Pipeline**:
   The current architecture leaps directly from `INVESTIGATE` to `HYPOTHESIZE`, and then provides no intermediate `SPECIFY` or `PLAN` stages. There is no phase transition rule that checks:
   - "Has a valid hypothesis been formed?"
   - "Is there a repair plan?"
   - "Are the target file lines inspected?"
   - "Transition phase to `PATCH` and instruct `apply_patch`."

3. **Absence of Reasoning-to-Action Steering**:
   When local models (like `qwen3:8b`) analyze code and state the solution in their thought block (e.g., "we must flatten the hooks in `__init__`"), they do not always spontaneously generate the full `apply_patch` JSON tool call in the same turn without an explicit phased prompt prompt asking for the SEARCH/REPLACE patch block.

---

## 5. Minimal Fix Architecture (Phase 2 - Phase 5)

### 1. Explicit Phase Lifecycle
```
INVESTIGATE
    ↓ (confidence threshold / circuit breaker)
SPECIFY
    ↓ (requirements & invariants defined)
HYPOTHESIZE
    ↓ (root cause & causal hypothesis validated)
PLAN
    ↓ (target file, symbols, and edits planned)
PATCH
    ↓ (apply_patch executed)
TEST
    ↓ (targeted tests & regressions evaluated)
DIAGNOSE / RETRY (if test fails)
    ↓
DONE
```

### 2. Specification Extraction (`RepairSpecification`)
- Lightweight model encapsulating:
  - `expected_behavior`, `observed_behavior`, `constraints`, `edge_cases`, `acceptance_criteria`, `target_tests`, `must_preserve`.
- Seeded from `ProgramUnderstanding` and refined during `SPECIFY`.

### 3. Reasoning & Action Output Handling
- Classify model outputs into:
  - `VALID_HYPOTHESIS`: extracts hypothesis, attaches to state, advances to `PLAN`.
  - `VALID_PLAN`: extracts edit targets, advances to `PATCH`.
  - `VALID_TOOL_CALL`: executes tool.
  - `REASONING_ONLY` / `MALFORMED_ACTION` / `NO_ACTION`: preserves thought in trajectory, constructs a concise steering prompt for the current phase, and continues the turn budget rather than aborting.

### 4. Explicit Phase Guards & Anti-Looping
- Prevent repeated reads or searches in `HYPOTHESIZE`/`PLAN`/`PATCH`.
- If a hypothesis and inspected code exist, enforce transition to `PLAN` and `PATCH`.
- If tests fail after `PATCH`, transition to `DIAGNOSE` to inspect tracebacks and retry up to bounded `max_patch_attempts`.
