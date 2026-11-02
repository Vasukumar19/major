# PatchForge AI — Full Findings Register (V1.8 Review)

**Document ID**: `DOC-AUDIT-V18-B`  
**Date**: October 12, 2026  
**Status**: Comprehensive Engineering Findings Inventory  

---

## Summary of Findings

| ID | Severity | Category | Target File & Lines | Confidence | Status |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **PF-COR-001** | **CRITICAL** | Correctness | `patchforge/pipeline/v1_orchestrator.py` L1126-1127, L852 | Confirmed | Active |
| **PF-SEC-001** | **HIGH** | Security | `patchforge/tools/execution.py` L60-67, L120-127 | Confirmed | Active |
| **PF-COR-002** | **HIGH** | Correctness / Architecture | `patchforge/pipeline/v1_orchestrator.py` L685, L790, L958 | Confirmed | Active |
| **PF-COR-003** | **HIGH** | Correctness | `patchforge/repair/ranking.py` L290 | Confirmed | Active |
| **PF-COR-004** | **HIGH** | Correctness / Reasoning | `patchforge/repair/structured_repair.py` L409-412; `v1_orchestrator.py` L856-860 | Confirmed | Active |
| **PF-COR-005** | **HIGH** | Correctness | `patchforge/repair/structured_repair.py` L593; `reconstructor.py` L493 | Confirmed | Active |
| **PF-SEC-002** | **MEDIUM** | Security | `patchforge/tools/search.py` L183-186 | Confirmed | Active |
| **PF-ARC-001** | **MEDIUM** | Architecture | `patchforge/core/orchestrator.py`, `core/orchestrator_v03.py`, `pipeline/baseline.py`, `pipeline/graph_engine.py` | Confirmed | Legacy / Parallel |
| **PF-ARC-002** | **MEDIUM** | Architecture / Dependencies | `third_party/aider_upstream`, `third_party/moatless_tools_upstream` | Confirmed | Inactive |
| **PF-EVAL-001** | **MEDIUM** | Evaluation Integrity | `patchforge/integrations/swebench.py` L238-276; benchmark cohort metadata | Confirmed | Active |
| **PF-PERF-001** | **LOW** | Performance | `patchforge/repository_intelligence/indexer.py` L95-104 | Confirmed | Active |
| **PF-MAINT-001**| **LOW** | Maintainability | Root repository directory (`patchforge.eval_*.json`) | Confirmed | Legacy Artifacts |

---

## Detailed Material Findings

### 1. PF-COR-001: Broken Partial Patch Preservation in V1 Orchestrator
- **Severity**: **CRITICAL**
- **Category**: Correctness / Logic
- **File & Lines**: `patchforge/pipeline/v1_orchestrator.py#L1126-L1127`, `patchforge/pipeline/v1_orchestrator.py#L852`
- **Affected Components**: `V1RepairOrchestrator`, `ResidualFailureRecord`, `StructuredRepairPromptBuilder`.
- **Evidence**: Source inspection of `patchforge/repair/schema.py` line 237 (`StructuredRepairOutput.replacement`) vs `v1_orchestrator.py` line 1126 (`structured_out.replacement_code`).
- **Description**: In `v1_orchestrator.py`:
  ```python
  if res_rec.is_partial_progress and getattr(structured_out, "replacement_code", None):
      last_working_replacement = structured_out.replacement_code
  ```
  `StructuredRepairOutput` defines the field as `replacement`, NOT `replacement_code`. Because `structured_out.replacement_code` does not exist, `getattr` returns `None`. Consequently, `last_working_replacement` is never populated. In subsequent refinement attempts (line 852), `prior_patch_code` is passed as `None`.
- **Actual vs. Expected Behavior**:
  - *Actual*: Partial repair progress achieved in attempt 1 or 2 is discarded; the model never receives its previously passing code.
  - *Expected*: The working replacement code is stored and supplied to the model as `prior_patch_code` to prevent behavioral regressions during residual repair.
- **Root Cause**: Naming mismatch introduced during the V1.7 refactor between the schema dataclass definition and orchestrator extraction logic.
- **Confidence**: Confirmed (100%).
- **Recommended Correction**: Update line 1126 to:
  ```python
  if res_rec.is_partial_progress and getattr(structured_out, "replacement", None):
      last_working_replacement = structured_out.replacement
  ```
- **Regression Test Requirement**: Add a targeted unit test verifying that when `ResidualFailureRecord.is_partial_progress` is `True`, `last_working_replacement` is assigned `structured_out.replacement` and rendered into the attempt 2 prompt.

---

### 2. PF-SEC-001: Host Execution Sandboxing Bypass via Temporary Directory
- **Severity**: **HIGH**
- **Category**: Security / Sandbox Integrity
- **File & Lines**: `patchforge/tools/execution.py#L60-L67`, `patchforge/tools/execution.py#L120-L127`
- **Affected Components**: `RunTargetedTestTool`, `RunReproductionTool`.
- **Evidence**: Source inspection of lines 60-67 and 120-127.
- **Description**: Both execution tools include the following logic:
  ```python
  if not allow_host and state and hasattr(state, "repo_dir") and state.repo_dir:
      import tempfile
      try:
          temp_root = Path(tempfile.gettempdir()).resolve()
          if Path(state.repo_dir).resolve().is_relative_to(temp_root):
              allow_host = True
      except Exception:
          pass
  ```
- **Security Threat**: Any repository checked out or cloned into the system temporary directory (e.g. `C:\Users\...\AppData\Local\Temp` or `/tmp`) automatically bypasses host execution protections and runs untrusted model-generated code directly on the host using `subprocess.run(["python", tf_name])`. A temporary folder on the host filesystem is not an execution isolation boundary; untrusted code has full access to the user's files, environment variables, credentials, and network sockets.
- **Actual vs. Expected Behavior**:
  - *Actual*: Untrusted model code executes directly on the developer's machine without warning whenever the workspace is inside `tempfile.gettempdir()`.
  - *Expected*: Untrusted model code executes only within isolated Docker/OCI containers unless the user explicitly opts in with `allow_host_execution=True`.
- **Confidence**: Confirmed (100%).
- **Recommended Correction**: Remove the `tempfile.gettempdir()` auto-opt-in check completely from lines 60-67 and 120-127.
- **Regression Test Requirement**: Test verifying that repositories situated inside `tempfile.gettempdir()` still raise `SECURITY_POLICY: Host execution of model-generated code is disabled by default` when `allow_host_execution=False`.

---

### 3. PF-COR-002: Multi-Site Repair Disconnect in Canonical V1 Orchestrator
- **Severity**: **HIGH**
- **Category**: Correctness / Architecture
- **File & Lines**: `patchforge/pipeline/v1_orchestrator.py#L685`, `L748`, `L790`, `L958`
- **Affected Components**: `V1RepairOrchestrator`, `RepairUnitPlanner`, `SourceReconstructor`.
- **Evidence**: Source code inspection and historical benchmark failures on multi-site tasks (`pallets__flask-4045`, `pytest-dev__pytest-5227`).
- **Description**: While `SourceReconstructor.reconstruct_multi` exists and `schema.py` defines `MULTI_SITE`, the V1 orchestrator pipeline is strictly single-unit:
  - Line 685: `selected_site = ranked_sites[0]` selects exactly one site.
  - Line 790: `RepairUnitPlanner.plan_repair_unit` generates exactly one `RepairUnit`.
  - Line 958: `SourceReconstructor.reconstruct_single` modifies only that single unit.
- **Actual vs. Expected Behavior**:
  - *Actual*: When a bug spans a caller and a callee, or a blueprint registration and an application handler, the secondary sites identified in `diagnosis.affected_sites` are discarded. The engine attempts to fix the bug exclusively by editing one site, guaranteeing verification failure.
  - *Expected*: For multi-site diagnoses, the orchestrator should plan non-overlapping `RepairUnit`s, prompt for sub-edits or coordinated replacements, and execute `reconstruct_multi`.
- **Confidence**: Confirmed (100%).
- **Recommended Correction**: Update `v1_orchestrator.py` to support multi-site units when `len(diagnosis.affected_sites) > 1` and dispatch to `reconstruct_multi`.

---

### 4. PF-COR-003: Basename-Only Target Reconciliation Collision Risk
- **Severity**: **HIGH**
- **Category**: Correctness
- **File & Lines**: `patchforge/repair/ranking.py#L290`
- **Affected Components**: `RepairSiteRanker.reconcile_target`.
- **Evidence**: Source inspection of line 290.
- **Description**: Line 290 checks:
  ```python
  if clean_file == af_f.replace("\\", "/").split("/")[-1].lower() and clean_sym == af_s.lower():
      return True, f"Target matches hypothesized affected site '{aff}'."
  ```
  `clean_file` is compared against `af_f.split("/")[-1]`. This checks only the basename of the file.
- **Consequence**: If a repository contains `requests/models.py` and `tests/models.py`, or `pytest/_pytest/assertion.py` and `testing/assertion.py`, candidate sites in the test directory or an unrelated subpackage can falsely reconcile against a diagnosis in the production package.
- **Actual vs. Expected Behavior**:
  - *Actual*: Basename collisions allow target reconciliation across different directory paths.
  - *Expected*: Target reconciliation must compare normalized relative paths from the repository root (e.g. `cand_file.endswith(af_f) or af_f.endswith(cand_file)`).
- **Confidence**: Confirmed (100%).
- **Recommended Correction**: Compare normalized paths rather than stripping to basename.

---

### 5. PF-COR-004: Strict No-Op Rejection Trap and Prompt Collapse
- **Severity**: **HIGH**
- **Category**: Correctness / Reasoning Failure
- **File & Lines**: `patchforge/repair/structured_repair.py#L409-L412`, `patchforge/pipeline/v1_orchestrator.py#L856-L860`
- **Affected Components**: `StructuredRepairParser.validate_semantic_repair`, `FailureDrivenReplanner`, `StructuredRepairPromptBuilder`.
- **Evidence**: V1.7 benchmark results for `psf__requests-863`, `pallets__flask-5063`, `psf__requests-2317`, `psf__requests-3362`, `pylint-dev__pylint-5859`, `pylint-dev__pylint-7114`, `pytest-dev__pytest-5221`, `pytest-dev__pytest-5227`.
- **Description**: In V1.7, 9 of 20 benchmark tasks failed with `UNRESOLVED` without executing a single test. The sequence:
  1. Attempt 1 prompt is evaluated. The model returns unchanged code.
  2. `validate_semantic_repair` rejects it: `SEMANTIC_VALIDATION_FAILURE: Replacement code is identical to original target code (no-op repair)`.
  3. `v1_orchestrator` falls back to `build_minimal_prompt` or `build_replacement_only_prompt`.
  4. These fallback prompts strip away surrounding context, contract specifications, and diagnosis rationale, presenting the model with only the bare target function and an error message.
  5. Starved of context, the model repeats the identical unchanged code across attempts 2 and 3.
  6. The run terminates with `patch_text: ""` and `f2p: 0/0`.
- **Consequence**: Tasks that previously received patches in V1.4 (such as `requests-863`) produce 0 patches and fail without evaluation.
- **Recommended Correction**: When a no-op failure occurs, do NOT collapse prompt context to minimal code. Instead, provide contrastive prompting (showing the target code and explicitly highlighting the unmet invariant) or widen the surgical target window.
- **Confidence**: Confirmed (100%).

---

### 6. PF-COR-005: Line-Continuation Syntax Corruption in Replacement Extraction
- **Severity**: **HIGH**
- **Category**: Correctness
- **File & Lines**: `patchforge/repair/structured_repair.py#L593`
- **Affected Components**: `StructuredRepairParser._repair_and_extract_json`.
- **Evidence**: V1.7 benchmark records for `pylint-dev__pylint-7228` and `pytest-dev__pytest-5103` producing `Function/Method syntax error: unexpected character after line continuation character`.
- **Description**: Line 593 uses:
  ```python
  cleaned = raw_rep.encode("utf-8").decode("unicode_escape", errors="replace")
  ```
  When Python source embedded in JSON strings contains trailing line-continuation backslashes `\` or raw regex backslashes, decoding with `unicode_escape` can introduce spaces or carriage returns after the backslash, generating `SyntaxError: unexpected character after line continuation character`.
- **Confidence**: Confirmed (100%).
- **Recommended Correction**: Use dedicated JSON string unescaping or AST string parsing rather than raw `unicode_escape` decoding on arbitrary Python code.

---

### 7. PF-SEC-002: Missing Path Containment in `FindSymbolTool`
- **Severity**: **MEDIUM**
- **Category**: Security
- **File & Lines**: `patchforge/tools/search.py#L183-L186`
- **Affected Components**: `FindSymbolTool.execute`.
- **Evidence**: Source inspection of lines 183-186.
- **Description**:
  ```python
  if file_path:
      full_file = repo_path / file_path
      if full_file.exists():
          files_to_check.append((file_path, full_file))
  ```
  Unlike `ReadFileTool` and `ApplyPatchTool`, `FindSymbolTool` does not validate `file_path` with `_resolve_and_contain`. An attacker could supply `file_path = "../../sensitive.py"` to parse AST and extract symbol information from files outside the authorized repository root.
- **Recommended Correction**: Call `_resolve_and_contain(repo_path, file_path, must_exist=True)` before checking `full_file.exists()`.
- **Confidence**: Confirmed (100%).

---

### 8. PF-ARC-001: Parallel Dead Orchestration Engines
- **Severity**: **MEDIUM**
- **Category**: Architecture / Maintainability
- **File & Lines**: `patchforge/core/orchestrator.py`, `patchforge/core/orchestrator_v03.py`, `patchforge/pipeline/baseline.py`, `patchforge/pipeline/graph_engine.py`
- **Affected Components**: Whole repository.
- **Evidence**: Inventory of 5 separate orchestrator implementations. `V1RepairOrchestrator` is the canonical active pipeline. Older engines rely on outdated schemas, lack V1.8 security containment, and create maintenance confusion.
- **Recommended Correction**: Explicitly deprecate and document legacy engines as historical baselines (`phase_d_v03`, `v05_baseline`), ensuring CLI runners default strictly to `v1`.
- **Confidence**: Confirmed (100%).

---

### 9. PF-ARC-002: 1,221 Unused Vendored Files in `third_party/`
- **Severity**: **MEDIUM**
- **Category**: Architecture / Hygiene
- **File & Lines**: `third_party/aider_upstream` (790 files), `third_party/moatless_tools_upstream` (431 files), and empty stub directories.
- **Evidence**: Full codebase symbol search confirms zero imports or invocations of `aider_upstream` or `moatless_tools_upstream`.
- **Recommended Correction**: Retain for historical baseline provenance if required, but formally declare as inactive/unlinked in repository architecture documentation.
- **Confidence**: Confirmed (100%).

---

### 10. PF-EVAL-001: SWE-bench Benchmark Metadata Discrepancy on `requests-863`
- **Severity**: **MEDIUM**
- **Category**: Evaluation Integrity
- **File & Lines**: `patchforge/integrations/swebench.py#L238-L276`, SWE-bench Lite dataset metadata.
- **Evidence**: In `SWE-bench/SWE-bench_Lite`, row `psf__requests-863` specifies `test_patch` adding `test_allow_list_of_hooks_to_register_hook`. However, its declared `FAIL_TO_PASS` list contains 4 existing tests (`test_POSTBIN_...`, `test_nonurlencoded_...`, `test_prefetch_...`, `test_urlencoded_...`) that pass on the base commit without applying any fix.
- **Impact on PatchForge**:
  - In V1.4, a cosmetic patch (`while 1` -> `while True`) was scored as `resolved: true` by the harness because those 4 tests passed.
  - In V1.7, strict no-op rejection produced an empty patch, causing the task to fail as `UNRESOLVED`.
- **Recommended Correction**: Rely on `validate_evaluation_consistency` to log task metadata anomalies without modifying official SWE-bench evaluation rules.
- **Confidence**: Confirmed (100%).

---

### 11. PF-PERF-001: Redundant Synchronous Test File Re-parsing on Cached Graph Load
- **Severity**: **LOW**
- **Category**: Performance
- **File & Lines**: `patchforge/repository_intelligence/indexer.py#L95-L104`
- **Evidence**: `RepositoryIndexer.index` re-reads all test files from disk synchronously on a cache hit instead of serializing the `TestGraphIndex` alongside the `RepositoryGraph`.
- **Recommended Correction**: Serialize `TestGraphIndex` to `cache_path.replace(".graph.json", ".test_index.json")`.
- **Confidence**: Confirmed (100%).

---

### 12. PF-MAINT-001: Root Directory Sprawl from Pre-V1.8 Prediction Files
- **Severity**: **LOW**
- **Category**: Maintainability
- **File & Lines**: Workspace root (`patchforge.eval_*.json`).
- **Evidence**: Over 150 prediction files exist in the root directory because `Tester.run()` historically lacked directory scoping before V1.8.
- **Recommended Correction**: Clean up obsolete prediction files in a future authorized workspace housekeeping batch.
- **Confidence**: Confirmed (100%).
