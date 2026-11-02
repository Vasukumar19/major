# CODEX HANDOFF — PatchForge AI Engineering Transition Guide

> [!IMPORTANT]
> **CRITICAL GROUND RULE FOR CODEX SESSIONS**:
> **Do not assume that an architectural component described in documentation is fully implemented. Verify every claim against source code and tests before modifying it.**
> 
> Always inspect the actual source code, verify active imports, and check test coverage before proposing, refactoring, or deleting components.

---

## 1. What PatchForge AI Is

**PatchForge AI** is an autonomous, evidence-grounded Automated Program Repair (APR) framework benchmarked on real-world Python repositories (the official 20-task SWE-bench Lite cohort).

Unlike unconstrained multi-turn chat agents that speculate and hallucinate in long conversational loops, PatchForge treats software repair as a **deterministic, contract-gated pipeline**:
1. Static code graph and test graph indexing.
2. Competing causal root-cause diagnosis.
3. Counterfactual contract gating.
4. Fully qualified candidate target ranking.
5. Structured JSON repair actions (`REPLACE_BLOCK`, `INSERT_BEFORE`, `INSERT_AFTER`, `WRAP_TRY_EXCEPT`).
6. AST-anchored surgical patch reconstruction.
7. Static syntax validation before container execution.
8. Official SWE-bench Docker test harness execution.
9. Failure classification and test-driven replanning.

---

## 2. Canonical Architecture & Authoritative Entry Points

- **Canonical Orchestrator**: `patchforge.pipeline.v1_orchestrator.V1RepairOrchestrator`
  - *Location*: [`patchforge/pipeline/v1_orchestrator.py`](file:///c:/Users/sandyarjun/Desktop/vasu_projects/major/patchforge/pipeline/v1_orchestrator.py)
  - Manages the entire issue-to-patch execution lifecycle.
- **Canonical Evaluation Entry Point**: `scripts/run_v05_cohort.py`
  - *Usage*: `python scripts/run_v05_cohort.py --instances <instance_id> --mode v1 --model qwen2.5-coder:14b`
  - `--mode v1` invokes `V1RepairOrchestrator` (Canonical V1.9).
  - `--mode baseline` invokes `BaselineRepairEngine` (Comparative baseline).
  - `--mode graph` invokes `GraphRepairEngine` (Experimental).
- **Secondary Entry Points**:
  - `scripts/run_task.py`: Single-task runner supporting agent and baseline modes.
  - `scripts/run_benchmark.py`: Alternative cohort evaluator.
- **Legacy Prototypes (Retained for Backwards-Compatibility)**:
  - `patchforge/core/orchestrator.py` (V0.2 prototype).
  - `patchforge/core/orchestrator_v03.py` (V0.3 prototype).
  - *Do not build new features on top of these prototypes. All new development belongs in `patchforge/pipeline/v1_orchestrator.py` or sub-modules.*

---

## 3. Directory Map & Component Roles

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
├── docs/                             # Comprehensive system documentation
├── CODEX_HANDOFF.md                  # This file
├── README.md                         # Project overview and quickstart
├── requirements.txt                  # Pinned Python package dependencies
├── .env.example                      # Template for local environment variables
└── .gitignore                        # Git exclusion rules protecting results and private data
```

---

## 4. How to Run the Project & Tests

### Environment Setup
```bash
# Activate virtual environment
source .venv/bin/activate  # Or on Windows PowerShell: .venv\Scripts\Activate.ps1

# Verify dependencies
pip install -r requirements.txt
```

### Running the Offline Test Suite
The entire test suite must run and pass offline:
```bash
python -m pytest tests -q
```
**Acceptance Baseline**: `332 passed, 82 warnings in ~25-45s`. (Zero failures permitted).

### Running a Real SWE-bench Task
Requires Ollama running with `qwen2.5-coder:14b` and Docker Engine running:
```bash
python scripts/run_v05_cohort.py --instances pallets__flask-4992 --mode v1 --model qwen2.5-coder:14b
```

---

## 5. Current Benchmark Performance Baseline

Evaluated on the frozen 20-task SWE-bench Lite cohort:
- **Model**: `qwen2.5-coder:14b` via local Ollama
- **Harness**: Official SWE-bench Docker containers (300s timeout)
- **Resolved Tasks**: **1 / 20 (5.0%)** (`pallets__flask-4992`)
- **FAIL_TO_PASS (F2P) Tests**: **32 / 54 passed (59.3%)** (16x increase over V1.8)
- **PASS_TO_PASS (P2P) Safety**: **1,081 / 1,115 passed (97.0%)** (62% reduction in regressions)
- **Patch Validity / Submission Rate**: **20 / 20 valid unidiffs (100.0%)**

---

## 6. Known Defects & Incomplete Components

When taking over development, focus on these verified limitations:

1. **Multi-Assertion Sub-Test Incompleteness**:
   - In tasks like `psf__requests-2148` and `pytest-dev__pytest-5227`, the patch resolves 2 of 3 failing assertions, but SWE-bench marks the entire task unresolved.
   - *Fix needed*: Implement test-guided micro-refinement in `replanner.py` to target residual failing assertions iteratively.
2. **AST Span Alignment on Complex Expressions**:
   - `SourceReconstructor` uses `ast.parse` line numbers. For multiline decorators and nested lambdas, line replacement fallback can introduce minor indentation flaws.
   - *Fix needed*: Transition from standard library `ast` to `libcst` for concrete syntax tree rewriting.
3. **Multi-Site Defect Coordination**:
   - Current `V1RepairOrchestrator` schedules single-target edits per attempt. Tasks requiring edits across 2 different files (`pylint-dev__pylint-7228`) cannot be solved in one transaction.
   - *Fix needed*: Support `CompositeRepairTarget` for atomic multi-file patching.
4. **Single-Process In-Memory Idempotency**:
   - `IngestionIdempotencyTracker` in `patchforge/integrations/github.py` uses an in-memory dictionary.
   - *Fix needed*: Add Redis or SQLite backend if deploying as a long-running web service.

---

## 7. Rules for Modifying the Codebase

1. **Strict Execution Discipline**:
   Always follow: **INSPECT → TEST → FIND ERRORS → FIX → RETEST → VERIFY**.
2. **Non-Destructive Git**:
   - Never run `git reset --hard` or `git checkout .`.
   - Never run `git clean -fd`.
   - Never delete historical benchmark results in `results/v17_orchestrator/`, `results/v18_orchestrator/`, `results/v19_orchestrator/`.
   - Never commit API keys, `.env` files, or private credentials.
3. **Preserve Established Interfaces**:
   - Do not break `PatchForgeWorkItem`, `RepairTarget`, `DiagnosisHypothesis`, or `RepairAction` schemas without updating all corresponding tests.
   - Retain backwards compatibility for CLI flags in `scripts/run_v05_cohort.py`.
4. **Offline Test Invariant**:
   - Every commit must pass all 332 tests in `tests/`.
   - If you add new features, add corresponding tests in `tests/`.

---

## 8. Files Codex Should Inspect First

1. [`patchforge/pipeline/v1_orchestrator.py`](file:///c:/Users/sandyarjun/Desktop/vasu_projects/major/patchforge/pipeline/v1_orchestrator.py): The canonical repair orchestrator.
2. [`patchforge/repair/structured_repair.py`](file:///c:/Users/sandyarjun/Desktop/vasu_projects/major/patchforge/repair/structured_repair.py): Structured JSON prompt construction and response parsing.
3. [`patchforge/repair/reconstructor.py`](file:///c:/Users/sandyarjun/Desktop/vasu_projects/major/patchforge/repair/reconstructor.py): AST span matching and surgical code modification.
4. [`patchforge/repair/replanner.py`](file:///c:/Users/sandyarjun/Desktop/vasu_projects/major/patchforge/repair/replanner.py): Failure classification and iterative repair decisions.
5. [`patchforge/diagnosis/contract_gate.py`](file:///c:/Users/sandyarjun/Desktop/vasu_projects/major/patchforge/diagnosis/contract_gate.py): Hypothesis selection policy and behavioral validation.
6. [`patchforge/repair/ranking.py`](file:///c:/Users/sandyarjun/Desktop/vasu_projects/major/patchforge/repair/ranking.py): Qualified target profile ranking.
7. [`docs/ROADMAP.md`](file:///c:/Users/sandyarjun/Desktop/vasu_projects/major/docs/ROADMAP.md): Detailed specifications for the next engineering milestones.
