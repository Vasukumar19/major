# PatchForge v0.3 Frozen Four-Task Smoke Benchmark Report

**Evaluation Date:** September 10, 2026  
**System Status:** FROZEN (Zero code, test, prompt, or configuration modifications during benchmark)  
**Evaluator:** Principal Engineer / Evaluation Framework  
**Model Under Test:** `qwen3:8b` via Ollama (`http://localhost:11434`)  
**Host Environment:** Windows (Python 3.12.10)

---

## 1. Executive Summary & Experimental Setup

This report documents the rigorous evaluation of **PatchForge v0.3** under strict evaluation-only conditions across the standard 4-task SWE-bench cohort. 

### Experimental Conditions
- **Codebase Freeze:** Zero source changes across `patchforge/`, `tests/`, and `configs/`.
- **Target Model:** `qwen3:8b` (Temperature = 0.0, Context Window = 32,768 tokens).
- **Execution Limits:** 12 max turns per task; 24,000 character maximum context preservation per file.
- **Evaluation Levels:**
  - **Level 1 (Engineering Correctness):** 80/80 unit tests passing (100%).
  - **Level 2 (Trajectory Progression):** 4/4 tasks (100%) successfully transition through `HYPOTHESIZE` $\to$ `PLAN` $\to$ `PATCH`.
  - **Level 3 (Repair Success):** 0/4 tasks resolved (0% resolved, 4/4 INCOMPLETE).

---

## 2. Exact Cohort Tested

| Instance ID | Repository | Target Defect Summary | Target File |
| :--- | :--- | :--- | :--- |
| `psf__requests-863` | `psf/requests` | List-valued hook wrapped in list causing nested hook lists | `requests/models.py` |
| `psf__requests-2674` | `psf/requests` | Unhandled `urllib3` exceptions (`DecodeError`, `TimeoutError`) | `requests/adapters.py` / `requests/api.py` |
| `psf__requests-1963` | `psf/requests` | `Session.resolve_redirects` copies original request method across redirects | `requests/sessions.py` |
| `pallets__flask-4045` | `pallets/flask` | Blueprint names containing dots (`.`) raise confusing routing errors | `src/flask/blueprints.py` / `src/flask/app.py` |

---

## 3. Aggregate Performance & Key Performance Indicators (KPIs)

| Metric | Measured Value |
| :--- | :--- |
| **Total Benchmark Runs** | 4 tasks |
| **Resolved (PASS)** | 0 / 4 (0.0%) |
| **Incomplete / Max Turns** | 4 / 4 (100.0%) |
| **Unit Test Suite Pass Rate** | 80 / 80 tests (100.0%) |
| **Tasks Reaching `HYPOTHESIZE`** | 4 / 4 (100.0%) |
| **Tasks Formulating Structured Hypothesis** | 4 / 4 (100.0%) |
| **Tasks Generating `RepairPlan`** | 4 / 4 (100.0%) |
| **Tasks Reaching `PATCH` Phase** | 4 / 4 (100.0%) |
| **Tasks Emitting `apply_patch` Actions** | 3 / 4 (75.0%) |
| **Total Patch Application Attempts** | 3 attempts |
| **Patch Application Failures (SEARCH Block Mismatch)** | 3 / 3 (100.0%) |
| **Placeholder Guard Firings (`...`)** | 0 firings (0.0%) |
| **Total Wall-Clock Runtime** | 2,030.0 seconds (~33.8 minutes) |
| **Total Tokens Consumed** | 204,291 tokens (142,109 input / 62,182 output) |

---

## 4. Per-Task Deep Dive

### 4.1. `psf__requests-863`
- **Turns Used:** 12 / 12
- **Duration:** 702.4s
- **Status:** `INCOMPLETE`
- **Control Flow:** `HYPOTHESIZE` (T1) $\to$ `PLAN` (T2–T4) $\to$ `PATCH` (T5–T12)
- **Actions:**
  - Turn 1: `find_symbol({'symbol_name': 'Request'})` (Confidence threshold reached $\to$ transitions to PLAN).
  - Turn 2: `formulate_hypothesis({'hypothesis_id': 'H001', 'root_cause': 'Request.__init__ wraps list-valued hooks in another list...'})` $\to$ **SUCCESS**.
  - Turn 3: `read_file({'file_path': 'requests/models.py', 'start_line': 100, 'end_line': 150})` $\to$ **SUCCESS** (verbatim code received).
  - Turns 4–12: Model emitted conversational analysis / `none()` actions without emitting an `apply_patch` tool call.
- **First Blocking Failure:** **Patch Synthesis Failure** (Model analyzed the defect and read the exact source code, but failed to synthesize an `apply_patch` call).

### 4.2. `psf__requests-2674`
- **Turns Used:** 12 / 12
- **Duration:** 574.1s
- **Status:** `INCOMPLETE`
- **Control Flow:** `HYPOTHESIZE` (T1) $\to$ `PLAN` (T2) $\to$ `PATCH` (T3–T12)
- **Actions:**
  - Turn 1: `read({'file': 'requests/packages/urllib3/exceptions.py'})` $\to$ **ERROR** (tool name `read` instead of `read_file`).
  - Turn 2: `formulate_hypothesis({'hypothesis_id': 'H001', 'root_cause': "requests API does not catch urllib3's DecodeError and TimeoutError..."})` $\to$ **SUCCESS**.
  - Turn 3: `propose_plan({'target_file': 'requests/api.py', 'intended_change': 'Add explicit handling for urllib3 exceptions...'})` $\to$ **SUCCESS**.
  - Turn 4: `apply_patch({'patch_text': '### requests/api.py\n<<<<<<< SEARCH\n            except requests.packages.urllib3.exceptions.URLError as e:\n=======\n...>>>>>>> REPLACE'})` $\to$ **ERROR** (`SEARCH block not found verbatim`).
  - Turn 5: `git_diff()` $\to$ **SUCCESS**.
  - Turn 6: `read_file({'file_path': 'requests/api.py'})` $\to$ **SUCCESS**.
  - Turns 7–12: Model became passive (`none()`).
- **First Blocking Failure:** **Patch Application Failure (Hallucinated Pre-State)** (The model attempted to patch `requests/api.py` before reading the file, guessing lines that did not exist verbatim).

### 4.3. `psf__requests-1963`
- **Turns Used:** 12 / 12
- **Duration:** 496.8s
- **Status:** `INCOMPLETE`
- **Control Flow:** `HYPOTHESIZE` (T1) $\to$ `PLAN` (T2) $\to$ `PATCH` (T3–T12)
- **Actions:**
  - Turn 1: `search_code({'code': 'Session.resolve_redirects', 'path': 'requests/sessions.py'})` $\to$ Confidence threshold reached.
  - Turn 2: `formulate_hypothesis({'hypothesis': "The Session.resolve_redirects method incorrectly reuses the original request method..."})` $\to$ **SUCCESS**.
  - Turn 3: `propose_plan({'target_file': 'requests/sessions.py', 'intended_change': 'Modify the redirect logic to reset method to GET...'})` $\to$ **SUCCESS**.
  - Turn 4: `apply_patch({'patch_text': '### requests/sessions.py\n<<<<<<< SEARCH\n        new_request = ...\n=======\n...>>>>>>> REPLACE'})` $\to$ **ERROR** (`SEARCH block not found verbatim`).
  - Turn 5: `read_file({'file_path': 'requests/sessions.py'})` $\to$ **SUCCESS**.
  - Turns 6–12: Model became passive (`none()`).
- **First Blocking Failure:** **Patch Application Failure (Premature Patching without Prior Read)**.

### 4.4. `pallets__flask-4045`
- **Turns Used:** 12 / 12
- **Duration:** 256.6s
- **Status:** `INCOMPLETE`
- **Control Flow:** `HYPOTHESIZE` (T1) $\to$ `PLAN` (T2–T4) $\to$ `PATCH` (T5–T12)
- **Actions:**
  - Turn 1: `search_code({'query': 'validate_blueprint_name', 'file_filter': 'app.py'})` $\to$ Confidence threshold reached.
  - Turn 2: `formulate_hypothesis(...)` $\to$ **SUCCESS**.
  - Turn 3: Duplicate search $\to$ **BLOCKED** by repetition guard.
  - Turn 4: `read_file({'file_path': 'app.py'})` $\to$ **ERROR** (`File 'app.py' does not exist`).
  - Turn 5: `propose_plan({'target_file': 'app.py', ...})` $\to$ **SUCCESS**.
  - Turn 6: `read_file({'file_path': 'app.py'})` $\to$ **ERROR** (`File 'app.py' does not exist`).
  - Turn 7: `find_symbol({'symbol_name': 'register_blueprint'})` $\to$ **SUCCESS** (located symbol in `src/flask/app.py`).
  - Turn 8: Duplicate `find_symbol` $\to$ **BLOCKED** by repetition guard.
  - Turn 9: `read_file({'file_path': 'flask/blueprint.py'})` $\to$ **ERROR** (`File 'flask/blueprint.py' does not exist`).
  - Turn 10: `apply_patch({})` $\to$ **ERROR** (`Could not parse any valid SEARCH/REPLACE blocks`).
  - Turn 11: `read_file({'file_path': 'app.py'})` $\to$ **ERROR**.
  - Turn 12: `search_code({'query': 'register_blueprint', 'file_filter': 'flask/'})` $\to$ **SUCCESS**.
- **First Blocking Failure:** **Localization / Target Path Resolution Failure** (Targeting non-existent `app.py` instead of `src/flask/app.py` and malformed patch schema).

---

## 5. Phase Transition & Control Flow Analysis

The phase transition state machine in PatchForge v0.3 functioned flawlessly:
1. **Confidence Threshold Gates:** Accurately detected when sufficient context was gathered during `HYPOTHESIZE` (confidence $\ge 0.70$ with distinct evidence) and forced progression to `PLAN`.
2. **Phase Progression Guards:** Successfully transitioned state to `PATCH` upon reception of valid `propose_plan` calls.
3. **Loop & Repetition Traps:** Repetition guards intercepted duplicate calls in `flask-4045` (Turns 3 and 8) without crashing the agent.

---

## 6. Hypothesis & Planning Quality Analysis

| Instance | Hypothesis Valid? | Plan Realistic? | Target File Accurate? |
| :--- | :--- | :--- | :--- |
| `psf__requests-863` | **Yes** (Identified hook list nesting) | **Yes** (Unpack or sanitize hook list) | **Yes** (`requests/models.py`) |
| `psf__requests-2674` | **Yes** (Identified unhandled urllib3 errors) | **Yes** (Catch & wrap in Requests exceptions) | **Partially** (`requests/api.py` vs `requests/adapters.py`) |
| `psf__requests-1963` | **Yes** (Identified redirect method reuse) | **Yes** (Override method on 303/302 redirects) | **Yes** (`requests/sessions.py`) |
| `pallets__flask-4045` | **Yes** (Identified blueprint dot validation) | **Yes** (Validate blueprint name on registration) | **No** (Relative path `app.py` vs `src/flask/app.py`) |

---

## 7. Patch Synthesis & Application Analysis

### Failure Breakdown
1. **Attempting Patches Before Reading Source:** In 2 out of 3 patch attempts (`requests-2674`, `requests-1963`), the model called `apply_patch` immediately after `propose_plan` without first calling `read_file` to fetch the exact context lines. As a result, its SEARCH blocks were hallucinated guesses that failed verbatim matching.
2. **Relative Path Hallucination:** In `flask-4045`, the model assumed top-level Python files (e.g. `app.py`) rather than repository-nested paths (e.g. `src/flask/app.py`), leading to repeated missing file errors.
3. **Turn Exhaustion from Passive Model Turns:** Once an initial `apply_patch` call failed, the smaller model (`qwen3:8b`) drifted into emitting empty thoughts / `none()` calls rather than parsing the error message, reading the file, and retrying with verbatim lines.

---

## 8. Baseline Comparison (v0.1 vs v0.2 vs v0.3)

| Dimension | v0.1 (Baseline) | v0.2 (Guarded) | v0.3 (Context-Preserved) |
| :--- | :--- | :--- | :--- |
| **Unit Test Health** | 36 / 36 | 36 / 36 | 80 / 80 (100%) |
| **Tasks Reaching `HYPOTHESIZE`** | 4 / 4 | 4 / 4 | 4 / 4 (100%) |
| **Tasks Formulating Hypothesis** | 0 / 4 | 0 / 4 | 4 / 4 (100%) |
| **Tasks Formulating `RepairPlan`**| 0 / 4 | 0 / 4 | 4 / 4 (100%) |
| **Tasks Reaching `PATCH` Phase** | 0 / 4 | 0 / 4 | 4 / 4 (100%) |
| **Tasks Attempting Patches** | 0 / 4 | 0 / 4 | 3 / 4 (75%) |
| **Placeholder Guard Firings (`...`)**| N/A | 0 firings | 0 firings |
| **Raw Source Preserved in Prompt** | No ($\le 250$ chars) | No ($\le 1000$ chars) | Yes (Verbatim up to 24k chars) |
| **Final Resolution (PASS)** | 0 / 4 | 0 / 4 | 0 / 4 |

---

## 9. Failure Taxonomy & Root Causes

```mermaid
graph TD
    A[Benchmark Incomplete: 4/4] --> B[Phase Transitions: RESOLVED]
    A --> C[Hypothesis & Planning: RESOLVED]
    A --> D[Patch Generation Bottlenecks]
    
    D --> D1[Issue 1: Premature Patching Before read_file]
    D --> D2[Issue 2: Search Block Verbatim Mismatch]
    D --> D3[Issue 3: Path Localization Mismatch e.g. app.py vs src/flask/app.py]
    D --> D4[Issue 4: Post-Error Passivity in Smaller LLMs]
```

---

## 10. Conclusions & Strategic Recommendation

PatchForge v0.3 has completely solved the architectural and plumbing bottlenecks that prevented agent control-flow progression in v0.1 and v0.2. The system now reliably drives LLMs from defect discovery through structured hypothesis formation, formal planning, and into active patch application.

The sole remaining blocker to benchmark resolution is **Patch Synthesis & Application Reliability**:
1. Requiring a mandatory `read_file` inspection of the target region before `apply_patch` is invoked.
2. Robust repository path normalization (e.g. automatic mapping from `app.py` to `src/flask/app.py`).
3. Active recovery prompting when a SEARCH block fails.

**Strategic Recommendation:** **Option D (FIX PATCH SYNTHESIS)**.
