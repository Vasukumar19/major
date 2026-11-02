# PatchForge AI — Reproducibility & Environment Record (V1.8 Review)

**Document ID**: `DOC-AUDIT-V18-G`  
**Date**: October 12, 2026  
**Audit Boundary**: Pure review and inspection; zero production code modified; no benchmark execution.

---

## 1. Git State and Checkout Integrity

- **Repository Root**: `c:\Users\sandyarjun\Desktop\vasu_projects\major`
- **Current Branch**: `main` (ahead of `origin/main` by 2 commits)
- **HEAD Commit**: `da203c81beeb4c3b1a687b1b953bd2d69a8b8e33`
- **HEAD Commit Message**: `feat(v1.5): improve assertion completeness, import healing, and empty-patch reliability`
- **Recent Git Log**:
  - `da203c8`: feat(v1.5): improve assertion completeness, import healing, and empty-patch reliability
  - `9c2bbc1`: feat(v1.4): achieve 4/20 resolved (20%) on SWE-bench cohort with 100% P2P regression safety
  - `c06b0b5`: docs: add reasoning failure dataset for the 13 wrong hypothesis tasks
  - `0fa241a`: feat(v1.3): implement causal diagnosis, behavioral trace modeling, information-directed probing, and repair completeness loop
  - `05c18b2`: feat(v1.2): implement algorithmic diagnosis, execution path modeling, and selective contract grounding

### Tracked Modifications in Working Tree (28 files)
The V1.6, V1.7, and V1.8 implementations exist as uncommitted working-tree modifications:
1. `patchforge/agent/controller.py`
2. `patchforge/diagnosis/candidate_analysis.py`
3. `patchforge/diagnosis/causal_trace.py`
4. `patchforge/diagnosis/diagnosis.py`
5. `patchforge/diagnosis/evidence.py`
6. `patchforge/experiment/counterfactual.py`
7. `patchforge/experiment/runner.py`
8. `patchforge/integrations/agentless.py`
9. `patchforge/integrations/minisweagent.py`
10. `patchforge/integrations/repograph.py`
11. `patchforge/integrations/swebench.py`
12. `patchforge/localization/localizer.py`
13. `patchforge/memory/store.py`
14. `patchforge/pipeline/v1_orchestrator.py`
15. `patchforge/repair/ranking.py`
16. `patchforge/repair/reconstructor.py`
17. `patchforge/repair/replanner.py`
18. `patchforge/repair/schema.py`
19. `patchforge/repair/structured_repair.py`
20. `patchforge/repository_intelligence/indexer.py`
21. `patchforge/repository_intelligence/test_graph.py`
22. `patchforge/retrieval/hybrid.py`
23. `patchforge/tools/editor.py`
24. `patchforge/tools/execution.py`
25. `patchforge/tools/search.py`
26. `patchforge/verification/tester.py`
27. `scripts/run_v05_cohort.py`
28. `tests/test_controller_recovery.py`

### Untracked Files Preserved
- **Engine Files**:
  - `README.md`
  - `requirements.txt`
  - `patchforge/repair/residual.py`
  - `tests/test_v16_pipeline_reliability.py`
  - `tests/test_v17_repair_quality.py`
  - `tests/test_v18_security_and_integrity.py`
- **User Documents & Artifacts**:
  - `"Soil Test Report – Red Soil, Piduguralla – Revised.docx"`
  - `build_portrait_work_plan_doc.py`
  - `build_work_plan_doc.py`
  - `deliverables/`
  - `output/`
  - `remove_remaining_wet_density.py`
  - `revise_density_report.py`
  - `tmp/`

---

## 2. Environment and Tooling Configuration

- **Host Operating System**: Microsoft Windows 11 Pro (win32)
- **Python Runtime**: Python 3.12.10 (64-bit)
- **Pytest**: 8.4.2
- **Core Dependencies**:
  - `networkx`: 3.4.2
  - `datasets`: 2.21.0
  - `requests`: 2.32.3
  - `swebench`: 2.0.0
- **Docker Environment**: Docker Desktop Windows (`C:\Program Files\Docker\Docker\resources\bin\docker.exe`)

---

## 3. Audit Verification Boundaries

### Verified in Current Checkout
1. **Compilation**: All 157 Python files (109 production, 43 test, 5 scripts) compiled cleanly via `py_compile` with zero syntax errors.
2. **Imports**: All 109 first-party modules in `patchforge` imported cleanly via `importlib.import_module`.
3. **Unit & Fast Suite**: `python -m pytest tests -q` executed: **264 passed, 82 warnings, 0 failed in 57.20s**.
4. **Historical Benchmark Logs**: Verified directly from `results/v1_orchestrator/summary.json` (4/20 resolved) and `results/v17_orchestrator/summary.json` (3/20 resolved).

### Explicitly Excluded Boundaries
1. **Full SWE-bench Benchmark**: The 3-hour 20-task benchmark was **not run**, in accordance with non-negotiable review constraints.
2. **External Model Inference**: No live Ollama / OpenRouter LLM calls were dispatched during this review.
3. **Upstream Modifications**: No changes were made to vendored upstream files in `third_party/`.
