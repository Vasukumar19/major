# PatchForge V0.4.3 — Multi-Site Repair Report

## Session Goal
Extend the V0.4 repair cohort to include multi-assertion Flask patches using the refinement loop, identify and fix two engine-level bugs that blocked progressive multi-cycle repair, and evaluate two new `psf__requests` instances.

---

## Engine Fixes (V0.4.3)

### Bug 1 — `failure_history` Pre-seeded with Cycle 0 Signature
**File**: `patchforge/pipeline/baseline.py`  
**Symptom**: Refinement loop terminated as `REFINEMENT_EXHAUSTED` after Cycle 1 when the same test name appeared in `fail_to_pass_failure` — even if the underlying assertion error had changed.  
**Root cause**: `failure_history` was initialized with the Cycle 0 verdict, so any refinement cycle that still failed the same tests (but at a different code site) triggered the "repeated failure" guard and broke early.  
**Fix**: `failure_history = []` — the guard now fires only when two *refinement* cycles produce identical failure signatures.

### Bug 2 — Regression-Gate Rollback Undid Intermediate Fixes
**File**: `patchforge/pipeline/baseline.py`  
**Symptom**: After Cycle 1 wrote the endpoint assertion fix to disk, the engine detected `is_better = False` (F2P count unchanged at 1/2) and rolled back to HEAD + Cycle 0 patch. Cycle 2 therefore saw the original unmodified assertion and generated the same Cycle 1 patch again, spinning in a loop.  
**Root cause**: `is_better` required strictly higher F2P count or P2P count to accept a refinement; lateral/partial progress (same score, no regression) triggered the rollback.  
**Fix**: Added `F2P >= prev AND P2P >= prev` as a fourth `is_better` condition. No-regression = patch retained on disk. The rollback branch now only fires for regressions.

---

## Cohort Scorecards

### ✅ `psf__requests-863` — GOLDEN BASELINE (V0.4)
| Metric | Result |
|--------|--------|
| FAIL_TO_PASS | **4/4** |
| PASS_TO_PASS | **60/60** |
| Refinement | 0 cycles |
| Status | **RESOLVED** |

> Preserved across all V0.4.x sessions.

---

### ✅ `pallets__flask-4045` — RESOLVED (V0.4.3)
| Metric | Result |
|--------|--------|
| FAIL_TO_PASS | **2/2** |
| PASS_TO_PASS | **50/50** |
| Refinement | **3 cycles** |
| Status | **RESOLVED** |

**Repair trajectory**:
| Cycle | Action | F2P | P2P | `is_better` |
|-------|--------|-----|-----|------------|
| 0 (initial) | `Blueprint.__init__` name check → `ValueError` | 1/2 | 50/50 | — |
| 1 | `add_url_rule` endpoint `assert` → `ValueError` | 1/2 | 50/50 | ✓ (no regression) |
| 2 | Model retry with wrong indentation → SEARCH_NOT_FOUND | — | — | skip |
| 3 | `view_func.__name__` `assert` → `ValueError` | **2/2** | **50/50** | ✓ RESOLVED |

**Target sites** (3 surgical edits across same file):
1. `blueprints.py::Blueprint.__init__` — dotted-name guard
2. `blueprints.py::add_url_rule` — endpoint assertion
3. `blueprints.py::add_url_rule` — view_func.__name__ assertion

---

### ⏳ `psf__requests-1963` — IN PROGRESS
| Metric | Result |
|--------|--------|
| Status | Running… |

---

### ⏳ `psf__requests-2674` — NOT STARTED
| Metric | Result |
|--------|--------|
| Status | Queued after 1963 |

---

## Engine Architecture (V0.4.3 State)

```
Issue
 ↓
Agentless-style localization (symbol-anchored, _COMMON_WORDS guard)
 ↓
Authoritative RepairTarget (primary + secondary EditSites)
 ↓
Aider-style compact context (VERIFIED EDITABLE SOURCE)
 ↓
Patch synthesis (qwen2.5-coder:7b @ Ollama)
 ↓
AST validation (validate_ast gate)
 ↓
Exact patch application (_apply_single_edit — 3-tier: Exact / WS-norm / Indent-norm)
 ↓
SWE-bench Docker evaluation
 ↓
Refinement loop (up to N cycles):
   ├─ extract_secondary_edit_sites (traceback-guided)
   ├─ failure_history guard (refinement cycles only)
   ├─ no-regression acceptance (F2P≥prev ∧ P2P≥prev → keep patch on disk)
   └─ rollback only on regression
```

### Key invariants:
- **Zero gold-patch leakage** in prompts or retrieval
- **Golden baseline preserved** (`psf__requests-863` always 4/4, 60/60)
- **`_cleanup()` isolation** — repo reset to HEAD after every `run()`
