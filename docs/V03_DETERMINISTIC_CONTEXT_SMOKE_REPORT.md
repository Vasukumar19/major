# PatchForge v0.3 — Deterministic Target Context Acquisition Smoke Report

## 1. Experiment Hypothesis
The model can synthesize patches more reliably if the controller deterministically supplies the exact target source context before allowing PATCH actions, eliminating the unverified context block-and-retry loop.

---

## 2. Exact Intervention
We implemented deterministic target context acquisition in `AgentController`:
```text
PLAN
 ↓
Is target source verified in verified_read_files?
 ↓
NO
 ↓
controller automatically invokes existing read_file
 ↓
exact source context stored in verified_read_files & trajectory
 ↓
ContextCompactor injects verbatim source into prompt
 ↓
PATCH phase
```
- **Files Modified**: `patchforge/agent/controller.py`
- **Helper Method**: `acquire_target_context(state, trajectory)`
- **Hooks**:
  1. Turn loop entry when `state.phase == AgentPhase.PATCH`
  2. Upon successful `propose_plan` tool execution
  3. Upon reasoning-driven transition from `PLAN` to `PATCH`

---

## 3. Tests Before / After
- **Before Intervention**: 95 passed / 5 failed (the 5 known external adapter fixture tests)
- **After Intervention**: 101 passed / 5 failed (6 new unit tests passing in `tests/test_deterministic_context.py`)
  1. `test_missing_context_automatically_acquired` — PASSED
  2. `test_existing_verified_context_not_reread` — PASSED
  3. `test_read_failure_does_not_verify_context` — PASSED
  4. `test_patch_remains_model_generated` — PASSED
  5. `test_hypothesis_and_plan_causality_preserved` — PASSED
  6. `test_no_architecture_bypass` — PASSED

---

## 4. Micro-Test Result
Tested `qwen2.5-coder:7b` in a single controlled PATCH turn with Issue, Hypothesis, RepairPlan, and verbatim source lines:
- `apply_patch emitted?` **YES**
- `parser accepted?` **NO** (model emitted unified diff `diff --git ...` rather than SEARCH/REPLACE format `### file\n<<<<<<< SEARCH\n...\n=======\n...\n>>>>>>> REPLACE`)
- `target file correct?` **NO** (under SEARCH/REPLACE parser)
- `SEARCH block grounded in visible source?` **NO**

---

## 5. Experimental Conditions
- **Model**: `qwen2.5-coder:7b` (via local Ollama at `http://localhost:11434`)
- **Temperature**: 0.0
- **Context Window**: 32,768
- **Turn Budget**: 12 turns per task
- **Timeout**: 1500s per task
- **Cohort**: `psf__requests-863`, `psf__requests-2674`, `psf__requests-1963`, `pallets__flask-4045`

---

## 6. Four-Task Cohort Trajectories & Results

| Task | PLAN | Auto Read | Verified Context | PATCH | apply_patch | Patch Applied | TEST | Resolved | Runtime (s) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| `psf__requests-863` | Turn 7-12 | N/A | No | No | No | No | No | No | 197.9 |
| `psf__requests-2674` | Turn 3-12 | N/A | No | No | No | No | No | No | 244.0 |
| `psf__requests-1963` | Turn 2-3 | Turn 2 | **Yes** (`requests/sessions.py`) | Turn 4-12 | No (natural prose) | No | No | No | 482.7 |
| `pallets__flask-4045` | Turn 3 | Turn 3 | **Yes** (`tests/test_blueprints.py`) | Turn 4-12 | No (natural prose) | No | No | No | 493.3 |

---

## 7. Analysis of Findings

1. **Deterministic Context Acquisition Functionality**:
   - In both `psf__requests-1963` and `pallets__flask-4045`, deterministic context acquisition successfully eliminated the previous `unverified_patch_blocks` guard intervention (0 unverified patch blocks vs. 4 in previous smoke).
   - Source code was fetched using existing `read_file` and injected into the prompt as verbatim lines.
2. **Patch Synthesis Failure Mode**:
   - When presented with verified source code in the `PATCH` phase, `qwen2.5-coder:7b` did not emit `apply_patch` JSON tool actions during full multi-turn conversations; instead, it emitted natural language markdown analysis (`### Analysis of the Code and Hypothesis...` and `### Repair Plan...`).
   - In the micro-test where tool calling was prompted, it generated a unified diff (`diff --git ...`) rather than the SEARCH/REPLACE block syntax required by the schema.
3. **SEARCH Block Failure Rate**:
   - Total patch attempts: 0
   - SEARCH block failures: 0
   - Rate: **N/A** (denominator is 0).

---

## 8. First Blocking Failure per Task

1. `psf__requests-863`: **Planning / Search wandering** (Model spent turns searching for symbol definitions in `requests.py` rather than formulating concrete file edit plan).
2. `psf__requests-2674`: **Planning / Search wandering** (Model spent turns querying `requests.packages.urllib3.exceptions` across git history).
3. `psf__requests-1963`: **Patch Synthesis** (Verified context was present, but model emitted prose analysis rather than `apply_patch` tool action).
4. `pallets__flask-4045`: **Patch Synthesis** (Verified context was present, but model emitted prose repair plan rather than `apply_patch` tool action).

---

## 9. Conclusion & Decision
Deterministic context acquisition successfully solved the controller-side context verification bottleneck ($\text{PLAN} \to \text{VERIFY CONTEXT}$). However, the model remains unable to reliably synthesize structured `apply_patch` JSON tool calls with SEARCH/REPLACE blocks.

**Single Decision**: **D — FIX PATCH SYNTHESIS**
