# PatchForge AI V0.4.3 — Control Analysis

This document audits the six algorithmic interventions introduced in V0.4.3.

---

## 1. Algorithmic Interventions Audit

### 1. `failure_history` Decoupling
- **Why it was added**:
  In V0.4.2, `failure_history` was pre-populated with Cycle 0's failure tuple `(fail_to_pass_failure, pass_to_pass_failure)`. When Cycle 1 executed and fixed a subset of assertions inside a test without yet passing the entire test name, the test name remained in `fail_to_pass_failure`. The engine treated this as an identical failure and aborted immediately with `REFINEMENT_EXHAUSTED`.
- **Failure solved**:
  Premature refinement termination when progress was made on internal assertions of a composite test.
- **New failure mode introduced**:
  If Cycle 1 and Cycle 2 produce genuinely identical failures, it now takes two refinement cycles instead of one to detect the loop and terminate.
- **Unit test coverage**:
  Covered in `tests/test_semantic_refinement_v04_2.py::test_repeated_failure_and_budget_guards`.
- **Benchmark evidence**:
  Allowed `pallets__flask-4045` to proceed past Cycle 1 to Cycle 3 where full resolution was achieved.

### 2. Non-Regressive Progress Acceptance (`is_better` Expansion)
- **Why it was added**:
  The previous rollback logic required strict inequality (`fail_to_pass_passed > prev` or `pass_to_pass_passed > prev`). Lateral progress (e.g. 1/2 F2P $\to$ 1/2 F2P with an internal assertion fixed) resulted in `is_better = False`, causing git rollback to HEAD and undoing the valid partial edit.
- **Failure solved**:
  Destructive git rollbacks that erased intermediate valid edits during multi-step repairs.
- **New failure mode introduced**:
  If a cycle makes an ineffective lateral change that neither regresses nor improves, the patch is retained, potentially biasing subsequent cycles with ineffective code.
- **Unit test coverage**:
  Covered in `tests/test_proven_architecture_v04.py` and refinement pipeline checks.
- **Benchmark evidence**:
  Crucial for `pallets__flask-4045`: retained the endpoint check in `add_url_rule` on disk so Cycle 3 could add the view function check.

### 3. Intermediate Patch Retention
- **Why it was added**:
  Multi-site repairs require accumulating changes across multiple files/methods incrementally.
- **Failure solved**:
  Inability of 7B models to synthesize multi-file edits in a single generation step.
- **New failure mode introduced**:
  Cumulative diff drift: if an earlier edit introduced subtle dead code, subsequent edits build on top of it.
- **Unit test coverage**:
  `tests/test_multisite_target_v04_3.py`.
- **Benchmark evidence**:
  Demonstrated in `pallets__flask-4045` (3 cumulative diffs cleanly committed to disk and evaluated).

### 4. Duplicate / No-Op Prevention
- **Why it was added**:
  Models frequently emit SEARCH and REPLACE blocks that are character-for-character identical when uncertain.
- **Failure solved**:
  Wasting test execution budget and Docker compute on no-op patches.
- **New failure mode introduced**:
  If a model intended to make no change to one block but modify another block in a multi-block patch, an overly aggressive check could misfire. (Mitigated by checking `all(s == r)` across all blocks).
- **Unit test coverage**:
  `tests/test_patch.py`.
- **Benchmark evidence**:
  Skipped no-op cycle in `psf__requests-1963` and `pallets__flask-4045`.

### 5. Patch Schema Agility (Unified Diff & Array Extraction)
- **Why it was added**:
  Compact models (`qwen2.5-coder:7b`) occasionally generate unified diffs (`diff --git`, `@@ -a,b +c,d @@`) or structured JSON lists `{"search": [...], "replace": [...]}` instead of verbatim Aider SEARCH/REPLACE blocks.
- **Failure solved**:
  False `PATCH_SYNTAX` failure classifications when semantically valid patches were present.
- **New failure mode introduced**:
  If the model emits array lines that omit non-contiguous lines (such as docstrings), exact line matching fails unless anchor relaxation is supported.
- **Unit test coverage**:
  `tests/test_robustness_v02.py`.
- **Benchmark evidence**:
  Successfully extracted patches for `psf__requests-2674`.

### 6. Test-Name Fallback Localization
- **Why it was added**:
  When pytest tests fail with a bare `AssertionError` without an in-library traceback frame, `FailureAnalyzer` had no traceback frames to expand secondary sites.
- **Failure solved**:
  Zero secondary site expansion for high-level assertion test failures.
- **New failure mode introduced**:
  Potential false-positive symbol matching if common words in test names match unrelated helper functions in the repository.
- **Unit test coverage**:
  `tests/test_multisite_target_v04_3.py`.
- **Benchmark evidence**:
  Correctly located `resolve_redirects` in `requests/sessions.py` from `test_requests_are_updated_each_time`.
