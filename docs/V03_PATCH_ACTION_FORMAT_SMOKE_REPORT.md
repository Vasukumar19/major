# PatchForge V0.3 — Deterministic Patch Action & Format Smoke Report

**Date**: 2026-09-19  
**Model**: `qwen2.5-coder:7b` (local Ollama, temp=0.0, context=32k, max_turns=12, timeout=1500s/task)  
**Experiment**: Deterministic Patch Action Selection & Format Fidelity Experiment  
**Frozen Cohort**: `psf__requests-863`, `psf__requests-2674`, `psf__requests-1963`, `pallets__flask-4045`

---

## Executive Summary

This experiment investigated whether clarifying the PATCH phase prompt contract, supporting flexible schema arguments in `ApplyPatchTool`, and adding deterministic output classification/recovery allows `qwen2.5-coder:7b` to progress from verified source context to accepted patch application and test execution.

1. **Micro-Test (`test_micro_patch_format.py`)**: Achieved **8/8 (100%)** success on all target criteria. When provided verified context with strict formatting rules, `qwen2.5-coder:7b` cleanly emitted `apply_patch` in valid JSON, accepted by both schema and parser, grounded against verified lines, and successfully applied to disk with valid AST.
2. **Frozen Smoke Benchmark (4 Tasks)**:
   - **4/4** tasks executed complete trajectories.
   - **3/4** tasks reached the `PATCH` phase (`psf__requests-863`, `psf__requests-1963`, `pallets__flask-4045`).
   - **1/4** emitted `apply_patch` (`psf__requests-863` at Turn 9).
   - **0/4** patches applied to the repository (1 invalid repo path `requests.py`, 2 tasks fell into prose loops during PATCH).
   - **0/4** reached `TEST` phase.
   - **0/4** resolved.

---

## 1. Controlled Micro-Test Results (qwen2.5-coder:7b)

| Metric / Criterion | Result | Evidence |
| :--- | :---: | :--- |
| **`apply_patch` emitted?** | **YES** | Action name parsed as `apply_patch` in JSON block |
| **Tool schema accepted?** | **YES** | Arguments parsed with valid `patch_text` string |
| **File path correct?** | **YES** | Targeted `requests/sessions.py` |
| **File verified?** | **YES** | Context present in prompt |
| **SEARCH/REPLACE format correct?** | **YES** | Valid markers (`<<<<<<< SEARCH`, `=======`, `>>>>>>> REPLACE`) |
| **SEARCH grounded?** | **YES** | Exact verbatim match in verified source |
| **Patch parser accepted?** | **YES** | `parse_edits()` extracted 1 edit block |
| **Patch applied?** | **YES** | `ApplyPatchTool` applied diff with valid AST syntax |

---

## 2. Frozen 4-Task Smoke Benchmark Summary Table

| Instance ID | Turns | Phase Reached | `apply_patch` Emitted? | Patch Format Valid? | Patch Applied? | Test Reached? | Resolved? | Blocking Failure Point |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| `psf__requests-863` | 12 | PATCH (T9) | YES (1) | YES | NO | NO | NO | **Path mismatch**: targeted `requests.py` instead of `requests/models.py` |
| `psf__requests-2674` | 12 | PLAN (T12) | NO | N/A | NO | NO | NO | **Turn exhaustion**: spent turns searching exception hierarchy in PLAN |
| `psf__requests-1963` | 12 | PATCH (T3) | NO | N/A | NO | NO | NO | **Prose deadlock**: emitted conversational analysis during PATCH (10 recoveries) |
| `pallets__flask-4045` | 12 | PATCH (T8) | NO | N/A | NO | NO | NO | **Prose deadlock**: emitted natural language reasoning in PATCH (5 recoveries) |

---

## 3. Comparative Evolution Across V0.3 Smoke Experiments

| Experiment | Model | Reached PATCH | `apply_patch` Emitted | Parser Accepted | Patch Applied | Reached TEST | Resolved |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Smoke 1 (Baseline V0.3)** | `qwen3:8b` | 4/4 (100%) | 3/4 (75%) | 0/4 (0%) | 0/4 (0%) | 0/4 (0%) | 0/4 (0%) |
| **Smoke 2 (Controller Recovery)** | `qwen3:8b` | 4/4 (100%) | 0/4 (0%) | 0/4 (0%) | 0/4 (0%) | 0/4 (0%) | 0/4 (0%) |
| **Smoke 3 (Deterministic Context)** | `qwen2.5-coder:7b` | 2/4 (50%) | 1/4 (25%) | 0/4 (0%) | 0/4 (0%) | 0/4 (0%) | 0/4 (0%) |
| **Smoke 4 (Deterministic Patch & Format)** | `qwen2.5-coder:7b` | **3/4 (75%)** | **1/4 (25%)** | **1/4 (25%)** | **0/4 (0%)** | **0/4 (0%)** | **0/4 (0%)** |

---

## 4. In-Depth Failure Analysis

### Task 1: `psf__requests-863`
- **Trajectory**: Investigated symbols $\to$ read file context $\to$ formulated H1 $\to$ proposed repair plan $\to$ reached `PATCH` phase at Turn 9.
- **Action**: Model emitted a valid SEARCH/REPLACE JSON `apply_patch` action.
- **Failure**: The model referenced `requests.py` as the target file rather than the submodule `requests/models.py`. Because `requests.py` did not exist at the repository root, `ApplyPatchTool` returned `Target file does not exist`. The model spent remaining turns searching for the file.

### Task 2: `psf__requests-2674`
- **Trajectory**: Formulated hypothesis in Turn 1 $\to$ attempted repeated code searches in PLAN to locate urllib3 exception wrapping logic across `requests/` modules.
- **Failure**: Did not converge on target source context before turn 12 limit.

### Task 3: `psf__requests-1963`
- **Trajectory**: Formulated hypothesis at Turn 1 $\to$ proposed repair plan at Turn 2 $\to$ entered `PATCH` at Turn 3.
- **Failure**: In multi-turn context (with previous conversational turns in trajectory), the model defaulted to generating natural language analysis (`### Repair Plan...`) rather than a JSON tool action. The controller guard blocked the empty action and injected feedback, but the model remained trapped in natural language responses.

### Task 4: `pallets__flask-4045`
- **Trajectory**: Investigated Blueprint symbols $\to$ read `src/flask/blueprints.py` $\to$ formulated hypothesis $\to$ proposed plan $\to$ entered `PATCH` at Turn 8.
- **Failure**: Similar to Task 3, after accumulating trajectory context, the model produced prose analysis (`Based on the provided code...`) instead of emitting the JSON `apply_patch` call.

---

## 5. Key Findings & Diagnostic Takeaway

1. **Micro-Test vs. Macro-Trajectory Discrepancy**:
   - In isolation (single turn, clean prompt), `qwen2.5-coder:7b` follows the SEARCH/REPLACE schema with **100% fidelity**.
   - In full multi-turn trajectories, accumulated chat history and thought-action formatting cause the model to regress into generating markdown prose or drifting on file paths (`requests.py`).
2. **Root Remaining Bottleneck**:
   - **Context Compaction & Forced Tool Invocation**: In multi-turn trajectories, previous natural language thoughts bias 7B coding models toward natural language outputs rather than strict JSON tool emission.

---

## 6. Recommendations & Next Steps

To bridge the gap between 100% micro-test performance and end-to-end benchmark execution:
1. **Strict Context Pruning in PATCH Phase**: When transitioning to `PATCH`, strip preceding discursive thoughts and supply only the concise Problem + Hypothesis + Plan + Target Source Context.
2. **Target File Path Grounding**: Ensure the target file path is explicitly bound from the RepairPlan / verified file cache so 7B models cannot hallucinate root-level file names (e.g., `requests.py`).
