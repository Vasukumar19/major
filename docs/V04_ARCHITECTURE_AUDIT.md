# PatchForge V0.4 — Architecture Audit & Recovery Plan

**Date**: 2026-09-19  
**Scope**: Complete architectural audit of PatchForge v0.3 and proven-agent recovery strategy for v0.4.  
**Production Constraint**: `qwen2.5-coder:7b` via local Ollama (temp=0.0, context=32k, max_turns=12).

---

## 1. Executive Summary

Over the past four benchmark runs and 10+ hours of testing, PatchForge v0.3 demonstrated severe operational bottlenecks:
- **0/4** tasks executed meaningful tests (`TEST` phase).
- **0/4** tasks resolved real defects.
- **0/4** tasks successfully modified repository source files during benchmarks.
- Several tasks suffered from runaway runtime (up to 766 seconds per task), exhausted all 12 turns in repetitive search loops, or deadlocked in natural language prose output during `PATCH`.

Yet, in our controlled single-turn micro-test (`test_micro_patch_format.py`), `qwen2.5-coder:7b` achieved **8/8 (100%)** success on patch action emission, schema acceptance, verbatim SEARCH grounding, AST validation, and clean application to disk.

**The core conclusion is unmistakable**:
> **The bottleneck in PatchForge is not the 7B coding model's synthesis ability. The bottleneck is the over-engineered, state-polluting controller loop that degrades model behavior over multi-turn trajectories.**

---

## 2. Current Execution Graph (v0.3)

```text
Problem (SWE-bench Issue)
   │
   ▼
OrchestratorV03 (repo checkout, Docker tester setup, tool registry with 13 tools)
   │
   ▼
AgentController.run()
   │
   ├── derive_understanding() [LLM call]
   ├── derive_specification() [heuristic]
   │
   ▼
TURN LOOP (Turns 1..12):
   │
   ├── 1. Evaluate Budget (AgentPolicy.evaluate_budget)
   │
   ├── 2. [If PATCH] acquire_target_context() -> read_file tool call
   │
   ├── 3. ContextCompactor.compact_prompt()
   │      └── Accumulates: System prompt (13 tools) + Issue (2000c) +
   │          Understanding + Specification + Visited snippets (3 files) +
   │          Key evidence (6 items) + Active hypothesis + Active plan +
   │          Probe results + Execution evidence + Raw file content (24k chars) +
   │          Recent tool actions & observations (last 3 steps)
   │
   ├── 4. LLM Generation (OllamaProvider: qwen2.5-coder:7b) [15-35s latency]
   │
   ├── 5. _parse_model_action() -> (thought, tool_call)
   │      ├── If no tool_call:
   │      │     ├── Phase auto-advance (INVESTIGATE -> HYPOTHESIZE -> PLAN -> PATCH)
   │      │     └── If in PATCH: PROSE_NO_TOOL / WRONG_PATCH_FORMAT guard blocks it,
   │      │         appends BLOCKED feedback to trajectory, and loops.
   │
   ├── 6. Evaluate Action Guards (AgentPolicy.evaluate_action)
   │      ├── Repetition guard
   │      ├── Zero-info streak guard
   │      ├── Phase-aware search trap guard
   │      └── apply_patch format & grounding guard
   │
   ├── 7. Execute Tool (ToolRegistry.execute)
   │      ├── Updates evidence store, visited files, visited symbols
   │
   ├── 8. If apply_patch:
   │      ├── If error: record patch failure, request read_file
   │      └── If success: run Tester (Docker) -> evaluate result -> policy decision
   │
   └── 9. Record step in AgentTrajectory -> Loop
```

---

## 3. Identification of Architectural Failure Loops & Redundancies

### A. The "Discursive Trajectory" Prompt Pollution (Prose Deadlock)
- **Observed in**: `psf__requests-1963` (10 turns in prose deadlock), `pallets__flask-4045` (5 turns in prose deadlock).
- **Mechanism**:
  1. During `INVESTIGATE`, the model emits natural thoughts and observations.
  2. When entering `PATCH`, `ContextCompactor` feeds the entire history of recent actions, thought summaries, and all 13 tool descriptions back into the model.
  3. The 7B model mimics its own preceding discursive style and responds with markdown prose explanations (`### Repair Plan...`) rather than an isolated JSON tool call.
  4. The controller classifies this as `PROSE_NO_TOOL`, records a `BLOCKED` status in the trajectory, and in the next turn feeds back the blocked message.
  5. The model sees the expanded transcript, generates another natural language apology/explanation, triggering an infinite recovery loop until turn 12 is exhausted.

### B. Target File Hallucination & Path Drift
- **Observed in**: `psf__requests-863` (Turn 9).
- **Mechanism**:
  1. The model investigated symbols in `requests/models.py`.
  2. In Turn 9, the model emitted `apply_patch` targeting `requests.py` (a non-existent file at repository root).
  3. `ApplyPatchTool` returned `Target file 'requests.py' does not exist`.
  4. Because target binding was not deterministic, the model spent turns 10-12 fruitlessly running `search_code` looking for `requests.py`.

### C. Search Loops in PLAN Phase
- **Observed in**: `psf__requests-2674` (Turns 2-12).
- **Mechanism**:
  1. The model formulated a hypothesis in Turn 1.
  2. In `PLAN`, the model repeatedly issued `search_code` queries to inspect exception hierarchies.
  3. The policy did not force-transition the agent from `PLAN` to `PATCH` once initial candidates were identified, burning all 12 turns in search queries without ever attempting an edit.

### D. Redundant Components & Tool Fragmentation
- PatchForge has **13 registered tools** exposed to the 7B model at every single turn:
  - `search_code`, `search_exact`, `find_symbol`, `find_references` (4 competing search tools)
  - `read_file`, `read_test` (2 reading tools)
  - `formulate_hypothesis`, `propose_plan`, `specify_repair` (3 reasoning tools that duplicate conversational thought)
  - `apply_patch`, `git_diff` (2 editing tools)
  - `run_targeted_test`, `run_reproduction`, `inspect_failure` (3 execution/debugging tools)
  - `inspect_graph`, `inspect_git_history` (2 graph tools)
- A 7B model gets easily confused when presented with 13 tool schemas simultaneously, leading to improper tool choice, schema syntax errors, or falling back to prose.

---

## 4. Runtime Breakdown & Sources of Latency

Across the frozen smoke benchmark:
- Total runtime: **2457 seconds (~41 minutes)** across 4 tasks.
- `psf__requests-863`: 714.2s (12 turns)
- `psf__requests-2674`: 470.2s (12 turns)
- `psf__requests-1963`: 766.2s (12 turns)
- `pallets__flask-4045`: 506.6s (12 turns)

**Source of Latency**:
1. **LLM Inference with Bloated Prompts**: ~20–35s per turn. When context approaches 15k–25k tokens due to raw file dumps and trajectory transcripts, local Ollama generation time triples.
2. **Unproductive Turns**: 12 turns executed per task because tasks failed to converge on `PATCH -> TEST` in turns 3–5. If a task reaches `PATCH` at turn 3 and tests at turn 4, total runtime drops to ~2–3 minutes!

---

## 5. Proven Systems Analysis & Design Mapping

| Proven System | Core Design Principle | How PatchForge Failed It | Recovery Action for PatchForge v0.4 |
| :--- | :--- | :--- | :--- |
| **mini-SWE-agent** | Minimal core (~100 lines), linear history, no state-machine traps. | PatchForge built a 746-line controller with complex multi-layer guard traps and state machines that lock into recovery loops. | Streamline agent loop into a strict, forward-progressing sequence. No repetitive recovery loops. |
| **SWE-agent** | Agent-Computer Interface (ACI) with model-friendly, unambiguous commands. | Exposed 13 tools simultaneously; model drifted between search and reasoning tools instead of editing. | Expose phase-specific, minimal toolsets. In `PATCH`, expose ONLY `apply_patch`. |
| **Agentless** | Strict phase isolation: `Localize -> Context -> Repair -> Test`. | Allowed arbitrary search in PLAN and carried huge investigation histories into the PATCH phase. | **Strict Context Isolation**: When in `PATCH`, clear all exploratory trajectory. Supply ONLY: Problem summary, target file, verified lines, and hypothesis. |
| **Aider** | Concise repository map & explicit file/symbol grounding. | Let model invent file paths from memory (`requests.py`), causing path mismatch errors. | **Deterministic Target Grounding**: Strictly bind target file path and symbol from localization/plan. Reject or auto-correct invalid paths. |
| **SWE-Doctor** | Structured test failure evidence driving iterative refinement. | Never reached `TEST`; when tests are run, structured `ExecutionEvidence` must feed the next refinement directly. | Wire Docker `Tester` directly to `TEST` phase with structured `FailureClass` feedback. |

---

## 6. PatchForge V0.4 Architecture Blueprint

### Principle 1: Strict Phase & Context Separation (Investigation vs. Repair)
- **Investigation Context**: Used in `INVESTIGATE` / `LOCALIZE`. Contains issue, search results, symbols, and code reads. Toolset limited to search/read tools.
- **Repair Context**: Used in `PATCH`. **Completely resets the prompt history**. Does NOT include the rambling multi-turn investigation transcript.
  ```text
  === REPAIR STATE ===
  Issue: <concise summary>
  Target File: requests/models.py
  Target Symbol: Request.__init__
  Active Hypothesis: <hypothesis description>
  Active Plan: <repair plan>
  Verified Source Context:
  <exact lines with line numbers>
  
  === ACTION REQUIRED ===
  Emit apply_patch targeting requests/models.py using SEARCH/REPLACE blocks.
  ```
  Only ONE tool is available: `apply_patch`.

### Principle 2: Deterministic Target Grounding
- The controller determines the exact target file path from localization / inspection evidence.
- If the model emits a patch with an incorrect file name (e.g. `requests.py` when target is `requests/models.py`), the controller automatically re-targets to the verified target path or rejects it immediately with the exact path required.

### Principle 3: Enforced Linear Progression
```text
UNDERSTAND (Turn 0)
    ↓
LOCALIZE / INSPECT (Turns 1-2, max 3)
    ↓
HYPOTHESIZE & PLAN (Turn 3)
    ↓
PATCH (Turn 4) [Clean Repair Context, apply_patch]
    ↓
TEST (Turn 5) [Run Docker test via Tester]
    ↓
[If Failed] REFINE (Turn 6) -> PATCH (Turn 7) -> TEST (Turn 8)
```
No task is allowed to spend 10 turns searching. If 3 turns elapse in investigation, the controller selects the top candidate and forces progression to `PATCH`.

### Principle 4: Deterministic Baseline Mode (Agentless-style Fallback)
- Provide a deterministic baseline execution mode alongside the agentic loop:
  `Localize -> Extract Target Source -> Synthesize Patch (single turn) -> Apply -> Docker Test`.
- This guarantees a benchmarked control condition.
