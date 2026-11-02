# PatchForge AI — Historical Benchmark Progression & Empirical Forensics

This document records the empirical evaluation history of PatchForge AI across all versions evaluated on the **frozen 20-task SWE-bench Lite cohort**.

---

## 1. Longitudinal Benchmark Results

| Version | Resolved Tasks | FAIL_TO_PASS (F2P) | PASS_TO_PASS (P2P) | P2P Pass Rate | Primary Architectural Focus |
|:---|:---:|:---:|:---:|:---:|:---|
| **V1.2** | 3 / 20 (15.0%) | 38 / 63 (60.3%) | *Historical report* | — | Algorithmic diagnosis & execution path modeling |
| **V1.4** | 4 / 20 (20.0%) | 39 / 63 (61.9%) | *Historical report* | 100.0% | Historical peak: selective contract grounding |
| **V1.7** | 3 / 20 (15.0%) | 29 / 36 (80.5%) | *Historical report* | — | Causal trace integration & multi-hypothesis ranking |
| **V1.8** | 1 / 20 (5.0%) | 2 / 37 (5.4%) | 585 / 675 | 86.7% | **Severe regression**: Contract Gate inverted selections |
| **V1.9 (Current)** | **1 / 20 (5.0%)** | **32 / 54 (59.3%)** | **1,081 / 1,115** | **97.0%** | **4 Root-Cause Fixes**: 16x F2P repair; 62% regression reduction |

---

## 2. Frozen 20-Task Benchmark Cohort

All evaluations are conducted against the identical 20-task cohort from SWE-bench Lite:

1. `pallets__flask-4045`
2. `pallets__flask-4992` *(Resolved in V1.9)*
3. `pallets__flask-5063`
4. `psf__requests-1963`
5. `psf__requests-2148`
6. `psf__requests-2317`
7. `psf__requests-2674`
8. `psf__requests-3362`
9. `psf__requests-863`
10. `pylint-dev__pylint-5859`
11. `pylint-dev__pylint-7114`
12. `pylint-dev__pylint-7228`
13. `pytest-dev__pytest-5103`
14. `pytest-dev__pytest-5221`
15. `pytest-dev__pytest-5227`
16. `pytest-dev__pytest-5495`
17. `pytest-dev__pytest-5692`
18. `pytest-dev__pytest-7220`
19. `pytest-dev__pytest-7373`
20. `pytest-dev__pytest-7490`

---

## 3. Evaluation Environment & Parameters

- **LLM**: `qwen2.5-coder:14b` (served locally via Ollama daemon)
- **Temperature**: `0.0` (deterministic sampling)
- **Context Limit**: 32,768 tokens
- **Inference Timeout**: 120 seconds per generation call
- **Harness**: Official SWE-bench Docker evaluation harness (`swebench==2.0.0`)
- **Container Timeout**: 300 seconds per task execution
- **OS Platform**: Windows 11 host with Docker Desktop on WSL2 (`desktop-linux`)
- **Patch Validity**: 20/20 tasks generated syntactically valid patches that applied cleanly in Docker (`100% Submission Rate`).

---

## 4. The V1.8 Regression & V1.9 Forensics

Between V1.7 (3/20 resolved, 29/36 F2P) and V1.8 (1/20 resolved, 2/37 F2P), performance collapsed. Forensic audit identified four interconnected root causes:

1. **Contract Gate Selection Inversion (`contract_gate.py`)**:
   - The contract gate used confidence-based sorting that preferred speculative counterfactual candidates over grounded primary diagnosis findings. In `psf__requests-2674`, the gate diverted repair from `requests/adapters.py` to `requests/utils.py`.
   - **V1.9 Fix**: Enforced a conservative gate policy preserving the primary diagnosis hypothesis unless counterfactual score $> 0.80$.

2. **Hypothesis Index & Rank Misalignment (`diagnosis.py`, `v1_orchestrator.py`)**:
   - Competing hypotheses had disjoint index and rank properties. Sorting hypotheses scrambled array indices, causing the orchestrator to pass the wrong hypothesis context to the repair planner.
   - **V1.9 Fix**: Enforced 0-based contiguous indexing and synchronized ranking order.

3. **Classifier & Replanner Import Healing Degradation (`classifier.py`, `replanner.py`)**:
   - Empty failure tracebacks with 0 F2P passed were misclassified as `TEST_FAILURE` rather than `WRONG_HYPOTHESIS`, preventing the replanner from switching hypothesis branches.
   - **V1.9 Fix**: Restored default `WRONG_HYPOTHESIS` fallback on empty tracebacks and maintained formatted reason strings.

4. **Candidate Profile Identity Qualification Collapse (`ranking.py`)**:
   - Candidates were indexed by simple function names (`send`). In `psf/requests`, this caused profile collision between `requests/adapters.py:HTTPAdapter.send` and `requests/sessions.py:Session.send`.
   - **V1.9 Fix**: Qualified all candidate identities: `file::class::function::action_target`.

---

## 5. V1.9 Empirical Results Analysis

The live benchmark execution of V1.9 (`results/v19_orchestrator/summary.json`) demonstrated dramatic partial repair recovery:

- **F2P Passed Tests**: Increased from 2 to **32 passed tests** (a **16x increase** over V1.8).
- **P2P Regression Safety**: Pass rate rose to **97.0%** (1,081 passed out of 1,115 tests), reducing regression failures from 90 to 34 (a **62% reduction**).
- **Fully Resolved Tasks**: Remained at **1/20** (`pallets__flask-4992`).

### Why Full Task Resolution Remained at 1/20
In SWE-bench, resolving a task requires **every single F2P sub-test to pass** with **zero P2P regressions**.
Detailed failure forensics revealed:
- In tasks like `psf__requests-2148` and `pytest-dev__pytest-5227`, PatchForge resolved 2 out of 3 failing assertions, but because the remaining assertion failed, SWE-bench scored the entire task as `UNRESOLVED`.
- In tasks like `pallets__flask-5063`, the LLM synthesized an accurate behavioral fix, but `SourceReconstructor` failed to align the exact multiline AST span, resulting in a fallback that missed secondary exception branches.
- In multi-file tasks (e.g. `pylint-dev__pylint-7228`), the fix required coordinated edits in two different files, which single-site bounded editing cannot complete in one attempt.

> [!IMPORTANT]
> **Evaluation Integrity**: Offline ablation experiments (such as testing hypothetical target selections against historical labels) must **never** be reported as live benchmark results. The numbers in Section 1 reflect genuine end-to-end executions through the official Docker test harness.
