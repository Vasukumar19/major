# PatchForge AI (V1.9)

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![SWE-bench Verified](https://img.shields.io/badge/eval-SWE--bench%20Lite-orange.svg)](https://www.swebench.com/)
[![Tests](https://img.shields.io/badge/tests-332%20passed-brightgreen.svg)](tests/)

**PatchForge AI** is an autonomous, evidence-grounded Automated Program Repair (APR) framework designed for real-world Python software repositories (benchmarked on the official SWE-bench Lite 20-task frozen cohort).

PatchForge shifts automated repair away from unconstrained multi-turn chat agents toward a **disciplined, contract-gated repair pipeline**: combining deterministic repository intelligence, competing causal diagnosis, counterfactual contract validation, AST-anchored surgical patch reconstruction, and test-driven failure classification.

---

## 1. Executive Summary & Capabilities

- **Deterministic Repository Intelligence**: AST symbol extraction, Call/Import/Heritage Code Graphs, and Test Graph mapping which links assertions and failure traces directly to candidate edit sites.
- **Competing Hypothesis Diagnosis**: Generates rival root-cause hypotheses with causal traces, predictions, and evidence packages rather than leaping to arbitrary code generation.
- **Contract Gate & Behavioral Requirements**: Evaluates API contracts, preconditions, and counterfactual constraints. In V1.9, the Contract Gate enforces a conservative selection policy that protects primary diagnosis findings against ungrounded speculation.
- **Qualified Candidate Ranking**: Solves candidate profile identity collapse by indexing target sites by fully qualified identities (`file::class::function::action_target`), eliminating cross-file target confusion.
- **Structured JSON Repair & AST Reconstruction**: Prompts the LLM (e.g. Qwen 2.5 Coder 14B) for structured JSON repair actions (`REPLACE_BLOCK`, `INSERT_BEFORE`, `INSERT_AFTER`, `WRAP_TRY_EXCEPT`) and maps them to concrete source code using AST span matching with robust whitespace-tolerant fallbacks.
- **Official SWE-bench Docker Harness Integration**: Executes candidate patches in isolated Docker containers running official repository test environments, enforcing 300s timeouts and capturing full stdout/stderr traces.
- **Normalized External Work Item Boundaries**: Ingests issues via normalized `PatchForgeWorkItem` models, supporting direct REST payloads, GitHub Webhooks (HMAC-SHA256 verified), and SWE-bench JSON records with strict path-traversal denial.

---

## 2. Technology Stack

- **Core Runtime**: Python 3.10+ (tested on Python 3.12, Windows 11 & Linux/WSL2)
- **Graph & Static Analysis**: `networkx`, Python standard library `ast`, `difflib`, `inspect`
- **LLM Inference**: Direct HTTP client to local [Ollama](https://ollama.ai/) daemon (default: `qwen2.5-coder:14b` with 120s timeout)
- **Container Harness**: Docker Engine (SWE-bench official environment images)
- **Data & Testing**: `datasets`, `swebench`, `requests`, `python-dotenv`, `pytest`, `pytest-timeout`

---

## 3. High-Level Architecture

```
                       Issue / Work Item (SWE-bench / GitHub / Direct)
                                             │
                                             ▼
                       ┌───────────────────────────────────────────┐
                       │  Input Ingestion & Path Traversal Guard  │
                       └─────────────────────┬─────────────────────┘
                                             │
                                             ▼
                       ┌───────────────────────────────────────────┐
                       │  Repository Intelligence & Test Indexer   │
                       └─────────────────────┬─────────────────────┘
                                             │
                                             ▼
                       ┌───────────────────────────────────────────┐
                       │   Hybrid Retrieval & Target Localization  │
                       └─────────────────────┬─────────────────────┘
                                             │
                                             ▼
                       ┌───────────────────────────────────────────┐
                       │  Competing Diagnosis & Hypothesis Engine  │
                       └─────────────────────┬─────────────────────┘
                                             │
                                             ▼
                       ┌───────────────────────────────────────────┐
                       │  Contract Gate & Behavioral Requirements  │
                       └─────────────────────┬─────────────────────┘
                                             │
                                             ▼
                       ┌───────────────────────────────────────────┐
                       │  Qualified Candidate Ranking & Planning   │
                       └─────────────────────┬─────────────────────┘
                                             │
                                             ▼
                       ┌───────────────────────────────────────────┐
                       │  Structured Repair & AST Reconstruction   │
                       └─────────────────────┬─────────────────────┘
                                             │
                                             ▼
                       ┌───────────────────────────────────────────┐
                       │   Static Validation (AST Syntax Parse)    │
                       └─────────────────────┬─────────────────────┘
                                             │
                                             ▼
                       ┌───────────────────────────────────────────┐
                       │    SWE-bench Docker Evaluation Harness    │
                       └─────────────────────┬─────────────────────┘
                                             │
                                             ▼
                       ┌───────────────────────────────────────────┐
                       │ Failure Classification & Residual Replan  │
                       └───────────────────────────────────────────┘
```

---

## 4. Current Benchmark Performance (Frozen 20-Task Cohort)

PatchForge is rigorously benchmarked against a frozen 20-task SWE-bench Lite cohort using local `qwen2.5-coder:14b`:

| Version | Resolved Tasks | F2P Passed Tests | P2P Safe Tests | Notes |
|:---|:---:|:---:|:---:|:---|
| **V1.2** | 3 / 20 (15%) | 38 / 63 (60.3%) | Historical report | Algorithmic diagnosis baseline |
| **V1.4** | 4 / 20 (20%) | 39 / 63 (61.9%) | 100% P2P safe | Historical peak resolved |
| **V1.7** | 3 / 20 (15%) | 29 / 36 (80.5%) | Historical report | Causal trace integration |
| **V1.8** | 1 / 20 (5%) | 2 / 37 (5.4%) | 585 / 675 (86.7%) | Severe regression due to contract gate inversion |
| **V1.9 (Current)** | **1 / 20 (5%)** | **32 / 54 (59.3%)** | **1,081 / 1,115 (97.0%)** | **16x increase in F2P repair; 62% reduction in regressions** |

*Note: In V1.9, the 4 root-cause regression defects were repaired, restoring partial repair capability from 2 to 32 passed F2P tests and eliminating 62% of regressions. Full task resolution remains bounded by multi-assertion sub-test incompleteness and AST span alignment on complex expressions.*

---

## 5. Quickstart & Installation

### Prerequisites
1. **Python 3.10+** (Python 3.12 recommended)
2. **Git** configured and accessible
3. **Ollama** installed and running:
   ```bash
   ollama serve
   ollama pull qwen2.5-coder:14b
   ```
4. **Docker Engine** running (for live test harness evaluation)

### Setup
```bash
# Clone the repository
git clone http://github.com/Vasukumar19/major.git
cd major

# Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
```

---

## 6. Running Tests

The test suite contains 332 fast offline unit and integration tests covering all pipeline stages:

```bash
# Run the complete test suite (approx. 25-40 seconds)
python -m pytest tests -q

# Run V1.9 specific verification tests
python -m pytest tests/test_v19_*.py -v

# Run security, integrity, and path-traversal tests
python -m pytest tests/test_v18_security_and_integrity.py -v
```

---

## 7. Running the SWE-bench Evaluation

To evaluate tasks from the SWE-bench Lite cohort:

```bash
# Run a single task through canonical V1.9 orchestrator
python scripts/run_v05_cohort.py --instances pallets__flask-4992 --mode v1 --model qwen2.5-coder:14b

# Run the complete frozen 20-task cohort
python scripts/run_v05_cohort.py --mode v1 --model qwen2.5-coder:14b

# Run with comparative baseline engine
python scripts/run_v05_cohort.py --instances psf__requests-863 --mode baseline
```

Task artifacts (generated patches, trajectories, test logs, and `summary.json`) are automatically persisted to `results/v19_orchestrator/`.

---

## 8. Documentation Index

Comprehensive technical documentation is organized in `docs/`:

| Document | Description |
|:---|:---|
| [**CODEX_HANDOFF.md**](CODEX_HANDOFF.md) | **Primary engineering handoff guide for OpenAI Codex sessions** |
| [**docs/PROJECT_CONTEXT.md**](docs/PROJECT_CONTEXT.md) | Project origin, problem definition, engineering philosophy, and non-goals |
| [**docs/ARCHITECTURE.md**](docs/ARCHITECTURE.md) | System design, component relationships, data/control flow, and Mermaid diagrams |
| [**docs/MODULE_REFERENCE.md**](docs/MODULE_REFERENCE.md) | Module-by-module reference, classes, functions, contracts, and error handling |
| [**docs/EXECUTION_FLOW.md**](docs/EXECUTION_FLOW.md) | Step-by-step walkthrough of an issue through the repair lifecycle |
| [**docs/REPOSITORY_INTELLIGENCE.md**](docs/REPOSITORY_INTELLIGENCE.md) | AST parsing, code graphs, test graphs, temporal churn, and hybrid retrieval |
| [**docs/REPAIR_AND_VERIFICATION.md**](docs/REPAIR_AND_VERIFICATION.md) | Diagnosis, contract gates, structured repair, AST reconstruction, and replanning |
| [**docs/INTEGRATIONS.md**](docs/INTEGRATIONS.md) | Normalized work items, GitHub webhooks, direct ingestion, and SWE-bench adapter |
| [**docs/CONFIGURATION.md**](docs/CONFIGURATION.md) | Environment variables, Ollama/Docker options, timeouts, and `.env.example` |
| [**docs/TESTING.md**](docs/TESTING.md) | Test hierarchy, running unit/integration suites, mock boundaries, and Docker |
| [**docs/BENCHMARK_HISTORY.md**](docs/BENCHMARK_HISTORY.md) | Longitudinal benchmark results (V1.2 through V1.9), metrics, and forensics |
| [**docs/KNOWN_LIMITATIONS.md**](docs/KNOWN_LIMITATIONS.md) | Detailed analysis of remaining failure modes and technical debt |
| [**docs/ROADMAP.md**](docs/ROADMAP.md) | Next engineering milestones (V2.0 test-guided micro-refinement, LibCST) |
| [**docs/DEVELOPMENT_GUIDE.md**](docs/DEVELOPMENT_GUIDE.md) | Developer setup, extending pipeline stages, adding tests, and debugging |
| [**docs/REPOSITORY_STABILIZATION_REPORT.md**](docs/REPOSITORY_STABILIZATION_REPORT.md) | Stabilization record, hygiene audit, and verification evidence |
