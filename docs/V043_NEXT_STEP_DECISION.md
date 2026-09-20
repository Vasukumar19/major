# PatchForge AI — Post-V0.4.3 Next Step Decision Artifact

This artifact establishes the empirical evidence, root cause forensics, open-source comparison, and strategic decision for the next evolutionary step of PatchForge AI.

---

## 1. Current Capability Status (V0.4.3 Frozen Checkpoint)

- **Commit Baseline**: `3b4a5f761603f5311723468b6df1d2732c59dd48`
- **Core Active Unit Tests**: 37/37 passing in 5.91s (`tests/test_patch.py`, `tests/test_robustness_v02.py`, `tests/test_multisite_target_v04_3.py`, `tests/test_proven_architecture_v04.py`, `tests/test_semantic_refinement_v04_2.py`).
- **Benchmark Cohort Outcome**:
  - `psf__requests-863`: **4/4 F2P, 60/60 P2P (RESOLVED)** — Preserved without regression.
  - `pallets__flask-4045`: **2/2 F2P, 50/50 P2P (RESOLVED)** — Complete 3-cycle multi-site resolution.
  - `psf__requests-1963`: **6/7 F2P, 111/112 P2P** — Near-miss state mutation defect.
  - `psf__requests-2674`: **0/12 F2P (PATCH_SYNTAX)** — Multi-line docstring omission in model SEARCH blocks.

---

## 2. Forensic Findings & Root Cause Analysis

### Bottleneck 1: Loop State Mutation / Inter-procedural Variable Lifetime (`psf__requests-1963`)
- **Symptom**: `test_requests_are_updated_each_time` fails with `assert 'POST' == 'GET'`.
- **Root Cause**:
  `resolve_redirects` in `requests/sessions.py` maintains an internal `while resp.is_redirect:` loop. In iteration 1, `req` is copied to `prepared_request`, and method changes from `POST` $\to$ `GET`. In iteration 2, the loop re-copied `prepared_request = req.copy()` from the original immutable `req` argument, resetting the method to `POST`.
- **Missing Capability**:
  The model attempts localized line edits (e.g. modifying `if method != 'HEAD'`) but fails to recognize that variable `req` must be reassigned (`req = prepared_request`) to propagate state across generator yields.
  Neither single-line prompting nor basic symbol-level extraction emphasizes loop-carried dependencies.

### Bottleneck 2: Interior Gap Omission in Model SEARCH Blocks (`psf__requests-2674`)
- **Symptom**: `apply_edits_detailed` returns `NOT_FOUND` because the model skipped intermediate docstrings in its emitted SEARCH block.
- **Root Cause**:
  The model output skipped the 6-line docstring between `def iter_content(...)` and `def generate():`, creating a non-contiguous match expectation.
- **Missing Capability**:
  Anchor-based multi-hunk search or docstring-stripped matching. When models emit code blocks with omissions, a strict contiguous line matcher rejects the edit.

---

## 3. Comparison with Upstream Implementations

| System | Upstream Source Inspected | Mechanism Analyzed | Relevance to PatchForge |
| :--- | :--- | :--- | :--- |
| **Agentless** | `agentless/util/postprocess_data.py` | `parse_str_replace_edit_commands`, `fake_git_apply_multiple`, `remove_empty_lines` | Agentless uses exact line replacement with fake git repo fallback and normalized empty lines. Highly robust against whitespace, but requires strict boundary matching. |
| **Moatless Tools** | `moatless/file_context.py` | `ContextFile`, `BlockSpan`, `_to_prompt_with_line_spans` | Moatless structures files into structured AST spans with explicit `span_id` markers and `... other code` foldings. This makes docstring gaps and foldings explicit to the LLM. |
| **Aider** | `aider/repomap.py` / `generator.py` | 3-tier matching + unified diff translation | Aider uses fuzzy search replacement with AST syntax confirmation. PatchForge's current 3-tier matcher closely reflects Aider's verified core. |
| **RepoGraph** | `repograph/construct_graph.py` | NetworkX call & definition graph query | Excellent for multi-file symbol dependency, but overkill for intra-method loop-variable dataflow. |

---

## 4. Evaluation of Candidate Interventions

1. **Candidate 1: Loop-Invariant & State-Flow Prompt Context (Targeting Bottleneck 1)**:
   - *Description*: When refinement diagnoses an assertion failure across iterations of a generator/loop (e.g. `AssertionError: assert 'POST' == 'GET'`), enrich the behavior context with explicit loop variable assignments and state mutations (`req`, `prepared_request`).
   - *Impact*: Directly targets the 6/7 $\to$ 7/7 near-miss on `psf__requests-1963`.
   - *Risk*: Low. Pure prompt/evidence enrichment without destructive mutation.

2. **Candidate 2: Relaxed Interior Docstring Matcher in `apply_edits_detailed` (Targeting Bottleneck 2)**:
   - *Description*: Allow `apply_edits_detailed` to match top and bottom anchor lines if the intermediate lines in the target file are purely comments or docstrings (`"""..."""`).
   - *Impact*: Solves SEARCH block omissions where the LLM skips docstrings between function signatures and inner definitions.
   - *Risk*: Low to moderate. Must be strictly bounded to prevent ambiguous matches.

3. **Candidate 3: Full Call-Graph / Data-Flow Subsystem (RepoGraph / CPG)**:
   - *Description*: Introduce an external graph database or full AST dataflow engine.
   - *Verdict*: **DEFERRED**. The bottleneck in `1963` is a single variable reassignment inside a 50-line method, not repository-scale graph navigation. Adding a heavy graph dependency is premature.

---

## 5. Strategic Next Step Decision

### Selected Primary Intervention:
**Combination of Relaxed Docstring Anchor Matching (Patch Application Robustness) and Loop State Evidence Diagnostics (Semantic Refinement).**

### Experiment Design:
1. Implement docstring-transparent anchor matching in `patchforge/repair/generator.py` to prevent `psf__requests-2674` schema omissions from failing.
2. In `patchforge/verification/analyzer.py`, detect loop/generator state patterns in execution evidence and explicitly guide the model to verify loop variable reassignment.
3. Validate against the frozen cohort:
   - `psf__requests-863` must remain **4/4 F2P, 60/60 P2P (RESOLVED)**.
   - `pallets__flask-4045` must remain **2/2 F2P, 50/50 P2P (RESOLVED)**.
   - `psf__requests-1963` target: **7/7 F2P, 112/112 P2P (RESOLVED)**.
   - `psf__requests-2674` target: eliminate `PATCH_SYNTAX` failure.

### Success & Rollback Criteria:
- **Success Criteria**: `psf__requests-863` and `pallets__flask-4045` 100% preserved; `psf__requests-1963` achieves $\ge$ 6/7 F2P; `psf__requests-2674` moves from `PATCH_SYNTAX` to executed evaluation.
- **Rollback Criteria**: Any regression on the 2 resolved tasks (`requests-863`, `flask-4045`) or introduction of AST syntax corruption triggers immediate rollback.
