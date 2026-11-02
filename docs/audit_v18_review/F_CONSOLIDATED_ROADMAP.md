# PatchForge AI — Consolidated Engineering Correction Roadmap (V1.8 Review)

**Document ID**: `DOC-AUDIT-V18-F`  
**Date**: October 12, 2026  
**Status**: Consolidated Architectural Plan for Next Engineering Batch  

---

## Roadmap Overview

Rather than issuing fragmented micro-patches, this roadmap consolidates all confirmed findings into **four coherent, prioritized workstreams**. Each workstream groups related root causes, defines the exact files affected, outlines risks, and specifies objective acceptance criteria.

---

## Workstream 1: Repair-Loop Correctness, Invariant Preservation & Multi-Site Synthesis (Priority: CRITICAL)

### Objectives & Root Causes Included
- **PF-COR-001**: Broken partial-repair preservation in `V1RepairOrchestrator` (`structured_out.replacement_code` -> `structured_out.replacement`).
- **PF-COR-002**: Multi-site repair disconnect: connecting secondary diagnosed sites to `SourceReconstructor.reconstruct_multi`.
- **PF-COR-004**: No-op prompt collapse: preventing repeated identical code rejections by supplying contrastive invariants and expanding context.
- **PF-COR-005**: Line-continuation syntax errors in string extraction and unescaping.

### Exact Files Affected
- `patchforge/pipeline/v1_orchestrator.py`
- `patchforge/repair/structured_repair.py`
- `patchforge/repair/reconstructor.py`
- `patchforge/repair/schema.py`
- `patchforge/repair/replanner.py`

### Dependencies
None. This is self-contained within the core repair engine.

### Expected Improvements
- Allows partial repair progress (e.g. `requests-1963` with 6/7 F2P passed) to be reliably carried forward into subsequent attempts without regression.
- Unlocks true multi-site repairs across callers, helpers, and registrations (e.g. `flask-4045`, `pytest-5227`).
- Eliminates the no-op rejection loop that caused 9 tasks in V1.7 to terminate without test evaluation.
- Prevents line-continuation syntax errors on Python string replacements.

### Implementation Risk
Medium. Modifying prompt structures and multi-site coordination requires strict AST validation to prevent overlapping edits.

### Minimum Cross-Component Tests
- Multi-site repair test verifying that two distinct methods across different files are simultaneously edited and produce a coherent diff.
- Residual repair test confirming that `last_working_replacement` is populated and rendered into attempt 2 prompts when `is_partial_progress` is True.
- No-op recovery test proving that a rejected unchanged snippet receives enhanced contextual guidance in attempt 2 instead of a stripped-down minimal prompt.

### Acceptance Criteria
- Unit tests verify `last_working_replacement` is non-empty after partial progress.
- Multi-site unit tests pass without overlapping edit errors.
- Fast test suite passes with 0 failures.
- Can be validated safely without running the 3-hour benchmark.

---

## Workstream 2: Execution Sandboxing, Workspace Containment & Target Alignment (Priority: HIGH)

### Objectives & Root Causes Included
- **PF-SEC-001**: Host execution sandbox bypass via `tempfile.gettempdir()`.
- **PF-SEC-002**: Missing path containment validation in `FindSymbolTool`.
- **PF-COR-003**: Basename-only collision risk in `RepairSiteRanker.reconcile_target`.

### Exact Files Affected
- `patchforge/tools/execution.py`
- `patchforge/tools/search.py`
- `patchforge/repair/ranking.py`
- `tests/test_v18_security_and_integrity.py`

### Dependencies
None.

### Expected Improvements
- Ensures untrusted model-generated code never executes on the host machine without explicit, intentional opt-in (`allow_host_execution=True`), regardless of where the repository directory is located.
- Closes directory traversal risks in symbol search.
- Prevents false target reconciliation across unrelated packages that share common filenames.

### Implementation Risk
Low. Tightening security checks and path comparisons has minimal regression risk when accompanied by proper containment tests.

### Minimum Cross-Component Tests
- Test verifying that a repository created in `tempfile.gettempdir()` raises `SECURITY_POLICY` when executing reproduction scripts with `allow_host_execution=False`.
- Test verifying that `FindSymbolTool` rejects paths containing `../` or absolute escapes.
- Test verifying that `reconcile_target` rejects a candidate in `tests/models.py` when the diagnosis specifies `requests/models.py`.

### Acceptance Criteria
- Zero silent host execution bypasses in temporary directories.
- All path-accessing tools strictly call `_resolve_and_contain`.
- Security test suite passes.

---

## Workstream 3: Architecture Consolidation, Upstream Boundaries & Cache Hygiene (Priority: MEDIUM)

### Objectives & Root Causes Included
- **PF-ARC-001**: Deprecate and document legacy parallel engines (`orchestrator.py`, `orchestrator_v03.py`, `baseline.py`, `graph_engine.py`).
- **PF-ARC-002**: Document dormant upstream implementations in `third_party/` (`aider_upstream`, `moatless_tools_upstream`).
- **PF-PERF-001**: Synchronous test file re-parsing on cached graph load.
- **PF-MAINT-001**: Clean root-level evaluation prediction artifacts.

### Exact Files Affected
- `patchforge/pipeline/` (`__init__.py`, engine docs)
- `patchforge/core/` (`orchestrator.py`, `orchestrator_v03.py`)
- `patchforge/repository_intelligence/indexer.py`
- `scripts/run_v05_cohort.py`
- Repository root cleanup scripts / documentation

### Dependencies
Workstreams 1 and 2 should be finalized first.

### Expected Improvements
- Eliminates developer confusion regarding which engine is canonical.
- Enforces `--engine v1` as the primary production pipeline.
- Reduces graph cache load time by serializing `TestGraphIndex`.
- Cleans over 150 historical evaluation artifacts from the workspace root.

### Implementation Risk
Very Low. Primarily documentation, deprecation warnings, and cache serialization.

### Acceptance Criteria
- `scripts/run_v05_cohort.py` defaults strictly to `v1`.
- Legacy engines output explicit deprecation notices when invoked.
- Graph index load does not trigger disk I/O on unchanged test files.

---

## Workstream 4: Evaluation Provenance, Discrepancy Auditing & Baseline Verification (Priority: LOW / MONITORING)

### Objectives & Root Causes Included
- **PF-EVAL-001**: Track SWE-bench task/test-patch metadata discrepancies (e.g. `requests-863`).
- Preserve immutable evaluation reports and historical benchmark logs.

### Exact Files Affected
- `patchforge/integrations/swebench.py`
- `scripts/run_v05_cohort.py`
- Benchmark documentation

### Dependencies
Workstreams 1-3.

### Expected Improvements
- Diagnostic reporting highlights when task metadata specifies non-discriminating `FAIL_TO_PASS` test sets.
- Guarantees official SWE-bench evaluation scores are never rewritten or obscured.

### Implementation Risk
None. Diagnostic and verification-only.

### Acceptance Criteria
- Evaluation reports include supplementary consistency diagnostics alongside official harness scores.
- Zero changes to official harness prediction format.
