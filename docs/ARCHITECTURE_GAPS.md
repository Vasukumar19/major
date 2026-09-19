# PatchForge AI — Architecture Gaps & Bottleneck Analysis

**Date**: 2026-09-19  
**Auditor**: Principal Autonomous Engineer  

---

## 1. Executive Identification of Bottlenecks

Through empirical evaluation on the frozen 4-task SWE-bench benchmark, PatchForge has resolved infrastructure timeouts (0 timeouts), patch syntax failures (0 syntax errors), and patch application failures (100% initial apply rate).

However, three primary architectural gaps currently block higher resolution rates:

```text
GAP 1: Single-Site Target Bottleneck (Multi-Site Deficiency)
       Evidence: pallets__flask-4045
       Cause: Defect spans Blueprint.__init__ (line 180) AND Blueprint.add_url_rule (line 360).
              RepairTarget only supports a single contiguous span.

GAP 2: Small-Model SEARCH Block Meta-Diff Hallucination
       Evidence: psf__requests-2674
       Cause: When presented with the original diff in refinement prompts, 7B models hallucinate
              diff markers (`+`/`-`) inside the SEARCH block, causing ApplyPatchTool rejection.

GAP 3: Architecture Fragmentation & Subsystem Duplication
       Evidence: 4 competing orchestrator/loop architectures in the codebase.
       Cause: Coexistence of V0.1/V0.3 waterfall orchestrator, V0.3 AgentController, and V0.4
              BaselineRepairEngine without a single unified pipeline abstraction.
```

---

## 2. Detailed Gap Analysis

### Gap 1: Single-Site `RepairTarget` Limitation
- **Current State**: `RepairTarget` contains `file_path`, `symbol`, `line_start`, `line_end`, and `verified_source`. It represents exactly one contiguous range of code in one file.
- **Empirical Evidence**:
  - In `pallets__flask-4045`, the test `test_route_decorator_custom_endpoint_with_dots` fails because `bp.add_url_rule('/', 'custom.endpoint', ...)` triggers `assert '.' not in endpoint` (AssertionError) in `src/flask/blueprints.py` line 360, rather than raising `ValueError`.
  - The model correctly repaired `Blueprint.__init__`, but could not touch `add_url_rule` because `RepairTarget` only gave it lines 171-201.
  - In refinement cycle 1, the model re-generated the exact same patch because `add_url_rule` was completely invisible to it.
- **Required Solution**:
  - Introduce `MultiSiteRepairTarget` supporting a `primary_site: EditSite` and `secondary_sites: list[EditSite]`.
  - Extend `FailureAnalyzer` to extract `target_locus` from tracebacks (`src/flask/blueprints.py:360`) and dynamically bind secondary sites to the `MultiSiteRepairTarget`.

---

### Gap 2: Refinement Prompt Representation & Search Hallucination
- **Current State**: `build_refinement_context` includes `=== ORIGINAL PATCH ===` with the unified diff.
- **Empirical Evidence**:
  - In `psf__requests-2674`, `qwen2.5-coder:7b` copied diff lines with leading `+` into its SEARCH block:
    ```
    <<<<<<< SEARCH
            resp = self.send(prep, **send_kwargs)
    +    except requests.packages.urllib3.exceptions.DecodeError as e:
    =======
    ```
  - The disk repository does not contain the `+` lines, so `ApplyPatchTool` rejected the patch with `SEARCH_NOT_FOUND`.
- **Required Solution**:
  - In refinement context, present previous changes as a conceptual summary or formatted replacement rather than raw diff with `+`/`-` markers.
  - Add explicit negative constraints in the patch contract: `"NEVER include '+', '-', or diff markers inside the SEARCH block. The SEARCH block must quote disk code verbatim."`
  - Enhance `ApplyPatchTool` / `parse_edits` with a deterministic sanitizer that strips unintentional leading diff markers if the underlying line matches verbatim.

---

### Gap 3: Competing Pipelines & Duplicated Localizers
- **Current State**:
  - `patchforge/pipeline/baseline.py` has an embedded `localize()` method.
  - `patchforge/localization/localizer.py` has a separate BM25/keyword localizer.
  - `patchforge/agent/controller.py` runs a 13-tool agent loop.
  - `patchforge/core/orchestrator.py` runs an 8-phase waterfall.
- **Required Solution**:
  - Consolidate all localization logic into `patchforge/localization/` using the proven stem-normalized Agentless pattern.
  - Establish `BaselineRepairEngine` as the canonical repair engine, deprecating the legacy waterfall orchestrators (`orchestrator.py`, `orchestrator_v03.py`).

---

### Gap 4: Test Suite Gaps & Mock Fragility
- **Current State**:
  - `tests/test_adapters.py` failed when RepoGraph pkl or mini-swe-agent was missing.
  - Full test suite had 5 failures in obsolete V0.3 tests while V0.4+ suites pass 51/51.
- **Required Solution**:
  - Update legacy tests to mock third-party dependencies gracefully.
  - Ensure 100% of all unit tests pass without external file dependencies.
