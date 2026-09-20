# PatchForge AI V0.4.3 — Failure Forensics Analysis

This document provides a forensic failure analysis for the V0.4.3 benchmark cohort (`psf__requests-863`, `pallets__flask-4045`, `psf__requests-1963`, `psf__requests-2674`).

---

## 1. Failure Forensics Table

| Task | Phase | Failure | Evidence | Root Cause | Existing Capability | Missing Capability | Proposed Intervention |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **`psf__requests-863`** | Evaluation | None (RESOLVED) | 4/4 F2P, 60/60 P2P in Docker eval | Defect isolated to single function `Request.register_hook` | Exact SEARCH/REPLACE, AST validation, symbol localization | None required for this instance (Golden Baseline) | Maintain regression test invariant across all future changes |
| **`pallets__flask-4045`** | Refinement (Cycle 1 & 2) | Intermediate stagnation / false exhaustion | 1/2 F2P in Cycle 0, 1/2 F2P in Cycle 1; model regenerated identical endpoint patch in Cycle 2 | 1. `failure_history` seeded with Cycle 0 signature broke loop prematurely.<br>2. Rollback gate rolled back Cycle 1 edit because test count was equal (1/2 $\to$ 1/2). | MultiSiteRepairTarget, traceback frame extractor, AST syntax validation | Non-regressive patch retention; cycle deduplication without aborting alternate sites | **Fixed in V0.4.3**: Decoupled `failure_history`, accepted non-regressive progress (`F2P >= prev`), resolved in Cycle 3 (2/2 F2P, 50/50 P2P) |
| **`psf__requests-1963`** | Refinement (Cycle 1-3) | Missing related state mutation / partial resolution | 6/7 F2P, 111/112 P2P. Failing test: `test_requests_are_updated_each_time` (`assert 'POST' == 'GET'`) | In `SessionRedirectMixin.resolve_redirects`, the loop re-copied from immutable `req` rather than persisting state from mutated `prepared_request` (`req = prepared_request`). | Localizer pinpointed `models.py::register_hook` and test-name fallback found `sessions.py::resolve_redirects`. | Inter-procedural state flow / loop invariant reasoning across generator lifecycle | Data-flow and state mutation guidance in refinement prompt; call-chain tracking for generator functions |
| **`psf__requests-2674`** | Synthesis | PATCH_SYNTAX / Schema drift | Model output JSON had `{"action": "apply_patch", "search": [...], "replace": [...]}` omitting docstring | Model hallucinated array schema and omitted middle docstring lines in SEARCH block, causing `NOT_FOUND`. | Exact, whitespace-normalized, and indentation-normalized 3-tier matching | Multi-line anchor matching with internal ellipsis / relaxed interior matching or docstring pruning | Structured JSON repair fallback; docstring-insensitive search matching |

---

## 2. Detailed Forensic Analysis

### 2.1 `psf__requests-863` (Golden Baseline Verification)
- **Status**: **RESOLVED** (4/4 F2P, 60/60 P2P).
- **Edit Site**: `requests/models.py::Request.register_hook` (lines 142-162).
- **Trajectory**: Resolved in a single shot (Cycle 0) without requiring refinement.
- **Verification**: Zero regressions observed; AST check passed immediately.

### 2.2 `pallets__flask-4045` (Multi-Site Resolution Trajectory)
- **Status**: **RESOLVED** (2/2 F2P, 50/50 P2P).
- **Edit Sites Used**:
  1. `src/flask/blueprints.py::Blueprint.__init__` (lines 188-195): Checked blueprint name for dots.
  2. `src/flask/blueprints.py::Blueprint.add_url_rule` (lines 363-366): Checked endpoint for dots.
  3. `src/flask/blueprints.py::Blueprint.add_url_rule` (lines 367-372): Checked view function `__name__` for dots.
- **Oracle Leakage Audit**:
  - The primary site (`Blueprint.__init__`) was retrieved by `RepoMap` based on issue text keywords (`"Blueprint"`, `"name"`, `"dot"`).
  - The secondary site (`add_url_rule`) was retrieved from the pytest execution traceback of `test_route_decorator_custom_endpoint_with_dots`:
    `File ".../flask/blueprints.py", line 368, in add_url_rule: assert "." not in endpoint`
  - Zero gold-patch leakage detected. Every site was discovered dynamically from runtime error traces.

### 2.3 `psf__requests-1963` (Near-Miss 6/7 F2P)
- **Status**: **PATCH_SEMANTICS** (6/7 F2P, 111/112 P2P).
- **Failing Test**: `test_requests.py::TestRedirects::test_requests_are_updated_each_time`
  - Traceback snippet:
    ```python
    redirect_generator = session.resolve_redirects(r0, prep)
    for response in redirect_generator:
        assert response.request.method == 'GET'
    AssertionError: assert 'POST' == 'GET'
    ```
- **Behavioral Chain**:
  1. Initial request: `POST` with HTTP 303 (See Other) $\to$ `resolve_redirects` changes method to `GET`.
  2. Second redirect: HTTP 307 (Temporary Redirect) $\to$ preserves the current request method (`GET`).
  3. Actual code: In iteration 2, `prepared_request = req.copy()` copies from the *original* `req` where method is still `POST`!
  4. Expected fix: `req = prepared_request` before `self.send(...)` so subsequent redirects inherit state.
- **Why it failed**: The model attempted to remove `method != 'HEAD'` guards rather than reassigning `req = prepared_request`. The model lacked explicit visibility into the variable mutation across generator loop iterations.

### 2.4 `psf__requests-2674` (Schema & Grounding Analysis)
- **Status**: **PATCH_SYNTAX** (0/12 F2P).
- **Root Cause**:
  The model output returned JSON with array-based `search` and `replace` lists. While V0.4.3 added schema extraction for `search`/`replace` lists, the SEARCH block lines emitted by the model omitted 6 lines of intermediate docstrings between `def iter_content(...)` and `def generate()`.
  Consequently, `apply_edits_detailed` returned `SEARCH block not found verbatim or normalized`.
