# PatchForge AI — Confirmed Known Limitations & Defect Analysis

This document provides a rigorous, evidence-driven accounting of known limitations in PatchForge AI V1.9. Every limitation documented here is categorized as either a **Confirmed Limitation** (backed by runtime evidence or code analysis) or a **Suspected Limitation** (observed empirically but requiring further systematic ablation).

---

## 1. Confirmed Architectural Limitations

### A. Multi-Assertion Sub-Test Incompleteness (Confirmed)
- **Problem**: When a SWE-bench task contains multiple failing assertions or sub-tests (e.g. `psf__requests-2148` testing both socket timeouts and connection pool recycling), PatchForge's single-turn repair prompt often fixes only the primary symptom highlighted in the issue description.
- **Impact**: Even though 32 F2P tests passed in V1.9 (a 16x increase), tasks with multiple test assertions were classified as `UNRESOLVED` by SWE-bench's binary metric because 1 lingering assertion failed.
- **Evidence**: `results/v19_orchestrator/psf__requests-2148.json` records 2/3 F2P assertions passing, but task status is `false`.

### B. AST Span Alignment Fragility on Complex Expressions (Confirmed)
- **Problem**: `SourceReconstructor` relies on Python's built-in `ast` module to identify line spans (`lineno`, `end_lineno`). On chained decorator calls, complex generator expressions, or multiline boolean statements, `ast.parse` does not preserve exact whitespace or comments, forcing the reconstructor into line-bounded string replacement fallback.
- **Impact**: In 3 tasks (`pallets__flask-5063`, `pytest-dev__pytest-5227`, `pytest-dev__pytest-5495`), string replacement fallback produced slight indentation offsets or failed to replace the intended nested block.
- **Evidence**: Static validation caught and rejected 4 candidate patches during early attempts, consuming repair budgets.

### C. Single-Site Bounded Scope vs Multi-Site Defects (Confirmed)
- **Problem**: The canonical `V1RepairOrchestrator` schedules repair for one candidate `RepairTarget` per attempt. If a defect requires simultaneous, coordinated edits across two distinct files (e.g., updating a helper in `utils.py` and updating the caller in `sessions.py`), PatchForge cannot apply both in a single atomic transaction.
- **Impact**: Multi-file defects (e.g. `pylint-dev__pylint-7228`) cannot be fully resolved within the current bounded repair executor.

### D. Single-Process In-Memory Idempotency (Confirmed)
- **Problem**: `patchforge.integrations.github.IngestionIdempotencyTracker` stores processed GitHub delivery IDs in an in-memory dictionary.
- **Impact**: The cache does not survive process restarts. If the application crashes or restarts, duplicate deliveries cannot be detected.
- **Evidence**: `patchforge/integrations/github.py` lines 15–40 use a local Python dictionary without Redis/database persistence.

### E. Lack of Standalone HTTP Web Daemon (Confirmed)
- **Problem**: While `GitHubWebhookHandler` and `DirectWorkItemIngestionHandler` provide complete payload validation, HMAC signature verification, and schema mapping, they are implemented as in-process handler classes. There is no built-in FastAPI or Flask server process running on a network port.
- **Impact**: Integrating with GitHub webhooks in production requires writing an external web wrapper script (e.g., FastAPI route dispatching to `handler.handle_webhook`).

---

## 2. Model & Environmental Limitations

### A. Context Saturation & Edge Case Synthesis in 14B Models (Confirmed)
- **Observation**: Evaluating with `qwen2.5-coder:14b` locally via Ollama provides fast, private inference, but 14B parameter models frequently omit secondary edge-case branches (e.g. handling `None` vs empty string `""` vs invalid types) compared with 70B+ models or frontier proprietary models.
- **Evidence**: In `pytest-dev__pytest-7373`, the model generated an `isinstance(val, str)` check but forgot to import `collections.abc.Mapping`, causing a subsequent runtime `NameError`.

### B. Windows Host WSL Docker Subprocess Overhead (Confirmed)
- **Observation**: Running the SWE-bench evaluation harness from a Windows Python environment communicating with Docker Desktop running inside WSL2 incurs file I/O latency and path translation overhead (e.g. `/mnt/c/` path mappings).
- **Evidence**: Task runtimes averaged 250–350 seconds per instance, bounded largely by Docker volume mount overhead and pip dependency compilation inside containers.

---

## 3. Suspected Limitations

### A. Residual Failure Error Log Truncation
- **Hypothesis**: When feeding test failure output back into the LLM during retry attempts, passing raw 100-line pytest traceback dumps may dilute the model's focus, causing it to rewrite working parts of the patch rather than micro-refining the failing assertion.
- **Status**: Suspected; requires an ablation comparing full pytest stdout vs focused single-line assertion failure extraction.

### B. Temporal Churn Weight Tuning
- **Hypothesis**: The weight assigned to git commit churn ($w_{\text{churn}}$) in `HybridRepositoryRetriever` may over-prioritize frequently edited configuration files over stable core algorithms.
- **Status**: Suspected; current weights are hand-tuned heuristic values.
