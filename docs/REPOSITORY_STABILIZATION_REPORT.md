# PatchForge AI — Repository Stabilization & Codex Handoff Audit Report

**Date**: 2026-11-02  
**Author**: Principal Engineer (Antigravity)  
**Target Recipient**: OpenAI Codex Engineering Session  
**Repository**: `http://github.com/Vasukumar19/major`  
**Branch**: `main`  
**Starting Commit**: `da203c81beeb4c3b1a687b1b953bd2d69a8b8e33`  

---

## 1. Initial Repository State

Prior to stabilization, PatchForge AI had undergone extensive empirical evaluation on SWE-bench Lite (progressing from V1.2 through V1.9).
- **Working Tree**: Contained 34 tracked modified files, 6 new canonical test modules, 4 integration modules, and several uncommitted user documents (civil engineering/soil test reports).
- **Evaluation History**: Contained historical benchmark cohorts in `results/` (`v17_orchestrator/`, `results/v18_orchestrator/`, `results/v19_orchestrator/`).
- **Baseline Test Suite**: 332 tests passed across 51 test modules.

---

## 2. Final Repository Structure

```
major/
├── patchforge/
│   ├── pipeline/                     # Production repair orchestrators (v1_orchestrator.py is CANONICAL)
│   ├── diagnosis/                    # Competing diagnosis, contract gates, and causal trace analysis
│   ├── repair/                       # Ranking, structured repair synthesis, AST reconstructor, replanner
│   ├── verification/                 # Test failure classifier and Docker test executor
│   ├── repository_intelligence/      # AST parser, code graph, test graph, and temporal churn indexer
│   ├── retrieval/                    # Hybrid BM25/AST/test trace retriever
│   ├── integrations/                 # Work item schemas, GitHub webhooks (HMAC-SHA256), direct API, SWE-bench
│   ├── models/                       # Ollama HTTP inference client
│   ├── core/                         # Config (PatchForgeConfig) and RepairTarget definitions
│   ├── agent/                        # Multi-turn tool-calling agent controller (AgentController)
│   ├── tools/                        # Sandboxed tools (editor, search, execution, reasoning)
│   └── telemetry/                    # Timing, token counter, and cost logger
├── scripts/                          # CLI runners (run_v05_cohort.py is canonical)
├── tests/                            # 51 test modules; 332 offline tests (conftest.py sets Windows git config)
├── results/                          # Frozen benchmark evaluation cohorts (v17, v18, v19)
├── docs/                             # Comprehensive system documentation (15 Markdown documents)
├── CODEX_HANDOFF.md                  # Primary handoff guide for OpenAI Codex
├── README.md                         # Root entry point and quickstart
├── requirements.txt                  # Pinned dependencies (networkx, datasets, requests, python-dotenv, swebench, pytest)
├── .env.example                      # Template for environment configuration
└── .gitignore                        # Git exclusion rules protecting results and private data
```

---

## 3. Files Removed, Retained, and Consolidated

### A. Files Removed
- Ephemeral test artifacts from `.pytest-temp/`, `scratch/test_*.py`, and temporary output logs generated during runtime debugging.
- No production source files or benchmark records were destroyed.

### B. Files Retained
- **Canonical Orchestrator**: `patchforge/pipeline/v1_orchestrator.py`
- **Canonical Diagnosis & Contract Gate**: `patchforge/diagnosis/contract_gate.py`, `diagnosis.py`, `contract.py`, `behavior.py`
- **Canonical Repair Engine**: `patchforge/repair/ranking.py`, `structured_repair.py`, `reconstructor.py`, `replanner.py`, `schema.py`
- **Canonical Ingestion & Integrations**: `patchforge/integrations/work_item.py`, `github.py`, `direct.py`, `swebench.py`, `minisweagent.py`
- **Legacy Prototypes Retained for Compatibility**: `patchforge/core/orchestrator.py`, `patchforge/core/orchestrator_v03.py`, `patchforge/pipeline/baseline.py`, `patchforge/pipeline/graph_engine.py`
- **Historical Benchmark Results**: `results/v17_orchestrator/` (21 files), `results/v18_orchestrator/` (21 files), `results/v19_orchestrator/` (21 files), `results/v05_14b/`, `results/v05_baseline/`.
- **User Documents Protected**: Personal civil engineering files (`Soil Test Report – Red Soil, Piduguralla – Revised.docx`, work plan generators) were preserved on disk and safely excluded via `.gitignore`.

### C. Files Consolidated
- `requirements.txt`: Standardized with `python-dotenv>=1.0.0`, `networkx>=3.0`, `datasets>=2.16.0`, `requests>=2.28.0`, `swebench>=2.0.0`, `pytest>=8.0.0`, `pytest-timeout>=2.2.0`.
- `.gitignore`: Updated to explicitly whitelist frozen benchmark results (`!results/v17_orchestrator/`, `!results/v18_orchestrator/`, `!results/v19_orchestrator/`) while ignoring temporary scratch files and user civil documents.
- `.env.example`: Created with clean placeholders.

---

## 4. Architecture & Engineering Changes Summary

1. **Contract Gate Policy**: Inverted selection defect resolved; conservative policy preserves primary diagnosis hypothesis unless counterfactual score $> 0.80$.
2. **Hypothesis Index & Rank Consistency**: Synchronized 0-based indexing and rank order across `diagnosis.py` and `v1_orchestrator.py`.
3. **Qualified Target Identities**: Solved candidate profile collapse by indexing edit sites with `file::class::function::action_target`.
4. **Classifier & Replanner Healing**: Maintained `WRONG_HYPOTHESIS` fallback on empty failure tracebacks and formatted F2P counts in replanner reasons.
5. **External Ingestion Security**: Implemented path traversal denial in `work_item.py` and `direct.py`; constant-time HMAC-SHA256 signature verification in `github.py`.
6. **Platform Resilience**: Handled Windows WSL `E_ACCESSDENIED` in `minisweagent.py`; added global Windows Git safe.directory configuration in `tests/conftest.py`.

---

## 5. Documentation Created

Fifteen comprehensive Markdown documents were generated:
1. `README.md`: Project overview, capabilities, architecture diagram, benchmark summary, quickstart.
2. `docs/PROJECT_CONTEXT.md`: Origin, problem definition, engineering philosophy, scope, non-goals.
3. `docs/ARCHITECTURE.md`: High-level system design, data/control flow, Mermaid diagrams.
4. `docs/MODULE_REFERENCE.md`: Module-by-module reference covering all 19 `patchforge/` packages.
5. `docs/EXECUTION_FLOW.md`: Step-by-step issue lifecycle walkthrough.
6. `docs/REPOSITORY_INTELLIGENCE.md`: AST analysis, code graph, test graph, temporal churn, hybrid retrieval.
7. `docs/REPAIR_AND_VERIFICATION.md`: Diagnosis, contract gating, structured repair, AST reconstruction, replanning.
8. `docs/INTEGRATIONS.md`: Work items, GitHub webhooks, direct API, SWE-bench adapter, operational limitations.
9. `docs/CONFIGURATION.md`: Environment variables, Ollama/Docker options, timeouts, `.env.example`.
10. `docs/TESTING.md`: Test hierarchy, running tests, mock vs live boundaries.
11. `docs/BENCHMARK_HISTORY.md`: Longitudinal benchmark table (V1.2 through V1.9), forensics, empirical evidence.
12. `docs/KNOWN_LIMITATIONS.md`: Multi-assertion incompleteness, AST expression alignment, single-site boundaries.
13. `docs/ROADMAP.md`: V2.0 priorities (micro-refinement, regression bumper guard, LibCST, multi-site repair).
14. `docs/DEVELOPMENT_GUIDE.md`: Developer setup, extending components, debugging, code standards.
15. `CODEX_HANDOFF.md`: Primary handoff guide for OpenAI Codex sessions.
16. `docs/REPOSITORY_STABILIZATION_REPORT.md`: This stabilization report.

---

## 6. Testing & Validation Evidence

- **Test Suite Command**: `python -m pytest tests -q`
- **Result**: **332 passed, 82 warnings in 28.62s**
- **Test Modules**: 51 files covering unit, integration, security, and pipeline compatibility.
- **CLI Verification**: `run_v05_cohort.py`, `run_task.py`, and `run_benchmark.py` all verified with `--help`.
- **Application Startup**: `from patchforge.pipeline import V1RepairOrchestrator` imports cleanly with zero errors.

---

## 7. Security Audit Findings

- **Path Containment**: Verified via `test_v18_security_and_integrity.py` and `test_v19_phase5_external_integrations.py`. Absolute path escapes (`/etc/passwd`, `C:\Windows`) and directory traversals (`../../`) are rejected.
- **Webhook HMAC**: Verified via raw binary body hashing and `hmac.compare_digest`.
- **Credential Safety**: No API keys, personal access tokens, or `.env` files are tracked or staged.
- **Docker Isolation**: Containers run with isolated filesystems and timeout limits (300s).

---

## 8. Final Git Status & Commit Details

- **Branch**: `main`
- **Backup Branch**: `backup-pre-codex-handoff` created at `da203c81beeb4c3b1a687b1b953bd2d69a8b8e33`.
- **Commit Message**: `chore: stabilize PatchForge V1.9 and prepare Codex handoff`
