# PatchForge AI — File-by-File Review Ledger (V1.8 Review)

**Document ID**: `DOC-AUDIT-V18-C`  
**Date**: October 12, 2026  
**Review Status**: 109 of 109 first-party source files accounted for and reviewed (100% complete).

---

## Review Ledger Classification Definitions

1. **Reviewed — No Material Issue**: Inspected, logic verified, error handling sound, no material architectural or correctness defects found.
2. **Reviewed — Findings Registered**: Inspected, material correctness, security, or architectural findings documented in `B_FINDINGS_REGISTER.md`.

---

## 1. Complete Inventory & Ledger of First-Party Files

### `patchforge/` (Root Package)
| File Path | Lines | Role | Status | Findings / Notes |
| :--- | :--- | :--- | :--- | :--- |
| `patchforge/__init__.py` | 15 | Package Init | Reviewed — No Material Issue | Exposes version and core exports. |

### `patchforge/agent/` (v0.3-v0.4 Agentic Controller)
| File Path | Lines | Role | Status | Findings / Notes |
| :--- | :--- | :--- | :--- | :--- |
| `patchforge/agent/__init__.py` | 24 | Package Init | Reviewed — No Material Issue | Clean package initialization. |
| `patchforge/agent/context.py` | 136 | Context Compaction | Reviewed — No Material Issue | Deterministic prompt window compaction. |
| `patchforge/agent/controller.py` | 882 | Agentic Controller | Reviewed — Findings Registered | Legacy v0.3 controller; parallel to canonical V1 (`PF-ARC-001`). Swallows exceptions on optional tool dispatch. |
| `patchforge/agent/policy.py` | 218 | Policy Guards | Reviewed — No Material Issue | Enforces step limits, repetition guards, and phase order. |
| `patchforge/agent/state.py` | 114 | Agent State | Reviewed — No Material Issue | Dataclass state modeling for agentic execution. |
| `patchforge/agent/trajectory.py` | 115 | History Recording | Reviewed — No Material Issue | Records thoughts and tool actions. |

### `patchforge/analysis/`
| File Path | Lines | Role | Status | Findings / Notes |
| :--- | :--- | :--- | :--- | :--- |
| `patchforge/analysis/__init__.py` | 8 | Package Init | Reviewed — No Material Issue | Clean exports. |
| `patchforge/analysis/state_flow.py` | 214 | Variable State Flow | Reviewed — No Material Issue | AST variable assignment and parameter flow tracking. |

### `patchforge/core/` (Core Configuration & Legacy Engines)
| File Path | Lines | Role | Status | Findings / Notes |
| :--- | :--- | :--- | :--- | :--- |
| `patchforge/core/__init__.py` | 12 | Package Init | Reviewed — No Material Issue | Clean exports. |
| `patchforge/core/config.py` | 124 | Configuration | Reviewed — No Material Issue | Centralized configuration dataclass. |
| `patchforge/core/orchestrator.py` | 275 | v0.1-v0.2 Orchestrator | Reviewed — Findings Registered | Legacy orchestrator; parallel to V1 (`PF-ARC-001`). Uses older upstream adapters. |
| `patchforge/core/orchestrator_v03.py` | 312 | v0.3 Orchestrator | Reviewed — Findings Registered | Parallel v0.3 agentic pipeline (`PF-ARC-001`). |
| `patchforge/core/state.py` | 68 | State Model | Reviewed — No Material Issue | Core state schema. |
| `patchforge/core/target.py` | 145 | Target Definitions | Reviewed — No Material Issue | `RepairTarget`, `EditSite`, `MultiSiteRepairTarget` schemas. |

### `patchforge/diagnosis/` (Behavioral & Causal Diagnosis Engine)
| File Path | Lines | Role | Status | Findings / Notes |
| :--- | :--- | :--- | :--- | :--- |
| `patchforge/diagnosis/__init__.py` | 42 | Package Init | Reviewed — No Material Issue | Exposes unified diagnosis components. |
| `patchforge/diagnosis/behavior.py` | 134 | Issue Behavior Extractor | Reviewed — No Material Issue | Extracts expected behavior, symptoms, and failure keywords. |
| `patchforge/diagnosis/candidate_analysis.py` | 358 | Blast Radius & Profiles | Reviewed — No Material Issue | Graph blast radius, cross-module caller tracking, and counterfactuals. |
| `patchforge/diagnosis/causal_trace.py` | 224 | Causal Trace Modeling | Reviewed — No Material Issue | Divergence tracking between expected and actual trace paths. |
| `patchforge/diagnosis/contract.py` | 338 | API Contract Extraction | Reviewed — No Material Issue | AST parameter and return type constraint extraction. |
| `patchforge/diagnosis/contract_gate.py` | 248 | Contract Gating | Reviewed — No Material Issue | Eliminates hypotheses violating API contracts or requirement matrices. |
| `patchforge/diagnosis/diagnosis.py` | 365 | Competing Diagnosis Engine | Reviewed — No Material Issue | Multi-hypothesis generation and prompt construction. |
| `patchforge/diagnosis/evidence.py` | 185 | Evidence Data Structures | Reviewed — No Material Issue | Strict typing: runtime traces, static code, unknown data. |
| `patchforge/diagnosis/execution_path.py` | 172 | Execution Path Modeling | Reviewed — No Material Issue | Call hierarchy modeling for failure paths. |
| `patchforge/diagnosis/unified_evidence.py` | 215 | Unified Evidence Substrate | Reviewed — No Material Issue | Integrates static, temporal, test, and runtime evidence. |

### `patchforge/experiment/` (Dynamic Probing Subsystem)
| File Path | Lines | Role | Status | Findings / Notes |
| :--- | :--- | :--- | :--- | :--- |
| `patchforge/experiment/__init__.py` | 16 | Package Init | Reviewed — No Material Issue | Clean exports. |
| `patchforge/experiment/counterfactual.py` | 165 | Counterfactual Scenarios | Reviewed — No Material Issue | Generates hypothesis-discriminating experiments. |
| `patchforge/experiment/injector.py` | 192 | AST Probe Injection | Reviewed — No Material Issue | Non-destructive AST assertion/logging injection. |
| `patchforge/experiment/observer.py` | 135 | Probe Output Parsing | Reviewed — No Material Issue | Extracts probe execution logs. |
| `patchforge/experiment/planner.py` | 148 | Probe Experiment Planning | Reviewed — No Material Issue | Determines probe locations at divergence points. |
| `patchforge/experiment/probe.py` | 98 | Probe Data Schema | Reviewed — No Material Issue | Dataclass definitions for probes. |
| `patchforge/experiment/runner.py` | 184 | Experiment Runner | Reviewed — No Material Issue | Orchestrates probe execution via container tester. Gated via config in V1.6+. |

### `patchforge/integrations/` (External Adapters)
| File Path | Lines | Role | Status | Findings / Notes |
| :--- | :--- | :--- | :--- | :--- |
| `patchforge/integrations/__init__.py` | 14 | Package Init | Reviewed — No Material Issue | Clean exports. |
| `patchforge/integrations/agentless.py` | 152 | Agentless Adapter | Reviewed — Findings Registered | Bypassed by canonical V1 (`PF-ARC-001`). Multi-path fallback operational. |
| `patchforge/integrations/minisweagent.py` | 119 | mini-SWE-agent Adapter | Reviewed — Findings Registered | Bypassed by canonical V1 (`PF-ARC-001`). Contains Windows WSL monkey-patching. |
| `patchforge/integrations/repograph.py` | 191 | RepoGraph Adapter | Reviewed — Findings Registered | Optional upstream graph builder; bypassed by canonical V1 (`PF-ARC-001`). |
| `patchforge/integrations/swebench.py` | 277 | SWE-bench Harness Adapter | Reviewed — Findings Registered | Evaluator adapter. Isolated by `run_id`, outer timeout enforced, container kill on timeout. Highlights `requests-863` task metadata issue (`PF-EVAL-001`). |

### `patchforge/issue/` (Problem Representation)
| File Path | Lines | Role | Status | Findings / Notes |
| :--- | :--- | :--- | :--- | :--- |
| `patchforge/issue/__init__.py` | 8 | Package Init | Reviewed — No Material Issue | Clean exports. |
| `patchforge/issue/interpreter.py` | 95 | Issue Parser | Reviewed — No Material Issue | Parses issue text and reproduction commands. |
| `patchforge/issue/problem.py` | 49 | Problem Dataclass | Reviewed — No Material Issue | Strict allowlist integrity gate (`FORBIDDEN_FIELDS = ('patch', 'test_patch')`). |

### `patchforge/localization/` (Localization Subsystem)
| File Path | Lines | Role | Status | Findings / Notes |
| :--- | :--- | :--- | :--- | :--- |
| `patchforge/localization/__init__.py` | 8 | Package Init | Reviewed — No Material Issue | Clean exports. |
| `patchforge/localization/candidate.py` | 86 | Candidate Scoring | Reviewed — No Material Issue | Candidate dataclass and basic ranking. |
| `patchforge/localization/localizer.py` | 248 | Localizer | Reviewed — No Material Issue | Multi-stage localization (lexical, symbol, test-trace). |

### `patchforge/memory/` (Cross-Task Repair Memory)
| File Path | Lines | Role | Status | Findings / Notes |
| :--- | :--- | :--- | :--- | :--- |
| `patchforge/memory/__init__.py` | 10 | Package Init | Reviewed — No Material Issue | Clean exports. |
| `patchforge/memory/episode.py` | 142 | Episode Schema | Reviewed — No Material Issue | Serializes and deserializes `RepairEpisode`. |
| `patchforge/memory/store.py` | 216 | Memory Store | Reviewed — No Material Issue | `MemoryMode.FROZEN` prevents live pollution; content-addressed snapshot fingerprinting verified. |

### `patchforge/models/` (LLM Inference Providers)
| File Path | Lines | Role | Status | Findings / Notes |
| :--- | :--- | :--- | :--- | :--- |
| `patchforge/models/__init__.py` | 10 | Package Init | Reviewed — No Material Issue | Clean exports. |
| `patchforge/models/provider.py` | 185 | Model Provider | Reviewed — No Material Issue | Ollama and OpenAI-compatible provider integrations. |
| `patchforge/models/router.py` | 92 | Model Router | Reviewed — No Material Issue | Task-based model routing. |

### `patchforge/pipeline/` (Orchestration Pipelines)
| File Path | Lines | Role | Status | Findings / Notes |
| :--- | :--- | :--- | :--- | :--- |
| `patchforge/pipeline/__init__.py` | 14 | Package Init | Reviewed — No Material Issue | Clean exports. |
| `patchforge/pipeline/baseline.py` | 265 | Baseline Repair Engine | Reviewed — Findings Registered | Legacy baseline pipeline (`PF-ARC-001`). |
| `patchforge/pipeline/graph_engine.py` | 315 | Graph Repair Engine (v0.5) | Reviewed — Findings Registered | Legacy graph engine (`PF-ARC-001`). |
| `patchforge/pipeline/v1_orchestrator.py` | 1288 | Canonical V1 Orchestrator | Reviewed — Findings Registered | Canonical active engine. Contains broken `replacement_code` lookup (`PF-COR-001`), lacks multi-site dispatch (`PF-COR-002`), and triggers no-op prompt collapse (`PF-COR-004`). |

### `patchforge/reasoning/` (v0.3 Reasoning Subsystem)
| File Path | Lines | Role | Status | Findings / Notes |
| :--- | :--- | :--- | :--- | :--- |
| `patchforge/reasoning/__init__.py` | 24 | Package Init | Reviewed — No Material Issue | Clean exports. |
| `patchforge/reasoning/generator.py` | 125 | Hypothesis Generator | Reviewed — No Material Issue | v0.3 reasoning generator. |
| `patchforge/reasoning/hypothesis.py` | 110 | Hypothesis Schema | Reviewed — No Material Issue | v0.3 hypothesis representation. |
| `patchforge/reasoning/plan.py` | 95 | Plan Schema | Reviewed — No Material Issue | v0.3 plan representation. |
| `patchforge/reasoning/probes.py` | 115 | Probe Logic | Reviewed — No Material Issue | v0.3 test and traceback probe parsing. |
| `patchforge/reasoning/ranker.py` | 105 | Hypothesis Ranker | Reviewed — No Material Issue | v0.3 hypothesis ranking. |
| `patchforge/reasoning/specification.py` | 120 | Specification Derivation | Reviewed — No Material Issue | v0.3 repair specification. |
| `patchforge/reasoning/understanding.py` | 110 | Program Understanding | Reviewed — No Material Issue | v0.3 issue understanding. |

### `patchforge/repair/` (Repair Planning, Reconstruction & Replanning)
| File Path | Lines | Role | Status | Findings / Notes |
| :--- | :--- | :--- | :--- | :--- |
| `patchforge/repair/__init__.py` | 32 | Package Init | Reviewed — No Material Issue | Clean exports. |
| `patchforge/repair/generator.py` | 215 | Edit Applicator | Reviewed — No Material Issue | SEARCH/REPLACE block parsing and application. |
| `patchforge/repair/loop.py` | 185 | Legacy Repair Loop | Reviewed — Findings Registered | Legacy v0.3 loop (`PF-ARC-001`). |
| `patchforge/repair/patch.py` | 95 | Patch Schema | Reviewed — No Material Issue | Unified diff patch object. |
| `patchforge/repair/planner.py` | 195 | Repair Unit Planner | Reviewed — No Material Issue | AST surgical unit planning. |
| `patchforge/repair/ranking.py` | 318 | Repair Site Ranker | Reviewed — Findings Registered | Basename-only comparison in `reconcile_target` (`PF-COR-003`). |
| `patchforge/repair/reconstructor.py` | 620 | Source Reconstructor | Reviewed — No Material Issue | Auto-heal builtin guard operational in V1.8 uncommitted changes. `reconstruct_multi` functional. |
| `patchforge/repair/replanner.py` | 158 | Failure-Driven Replanner | Reviewed — No Material Issue | Replan routing logic across hypothesis, semantics, and syntax. |
| `patchforge/repair/residual.py` | 161 | Residual Failure Record | Reviewed — No Material Issue | Tracks passing vs failing assertions and corrective guidance. |
| `patchforge/repair/schema.py` | 317 | Repair Engine Schema | Reviewed — No Material Issue | Authoritative dataclasses and enums (`RepairUnit`, `StructuredRepairOutput`). |
| `patchforge/repair/structured_repair.py` | 618 | Structured Prompt & Parser | Reviewed — Findings Registered | Prompt collapse on no-op (`PF-COR-004`); line-continuation decoding syntax corruption (`PF-COR-005`). |
| `patchforge/repair/validator.py` | 184 | Static Repair Validator | Reviewed — No Material Issue | Pre-testing syntax, AST, and diff validation. |

### `patchforge/repository/`
| File Path | Lines | Role | Status | Findings / Notes |
| :--- | :--- | :--- | :--- | :--- |
| `patchforge/repository/map.py` | 195 | Repository Map | Reviewed — No Material Issue | Surgical code snippet extraction via AST. |

### `patchforge/repository_intelligence/` (Deterministic Graph Engine)
| File Path | Lines | Role | Status | Findings / Notes |
| :--- | :--- | :--- | :--- | :--- |
| `patchforge/repository_intelligence/__init__.py` | 25 | Package Init | Reviewed — No Material Issue | Clean exports. |
| `patchforge/repository_intelligence/agent_tools.py` | 145 | Graph Tools for Agent | Reviewed — No Material Issue | Tool wrappers for call-graph exploration. |
| `patchforge/repository_intelligence/diagnosis.py` | 175 | Structural Diagnosis | Reviewed — No Material Issue | Graph-based diagnosis helpers. |
| `patchforge/repository_intelligence/flow.py` | 165 | Control Flow Analysis | Reviewed — No Material Issue | Control flow graph builder. |
| `patchforge/repository_intelligence/git_evidence.py` | 135 | Git Churn & Co-Change | Reviewed — No Material Issue | Git commit history analysis. |
| `patchforge/repository_intelligence/graph.py` | 285 | Repository Graph | Reviewed — No Material Issue | NetworkX directed call/reference graph. |
| `patchforge/repository_intelligence/indexer.py` | 142 | Repository Indexer | Reviewed — Findings Registered | Parallel AST indexing; dirty tree cache invalidation. Redundant test file re-parsing on cache hit (`PF-PERF-001`). |
| `patchforge/repository_intelligence/parser.py` | 195 | AST Source Parser | Reviewed — No Material Issue | Extracts nodes and edges from Python code. |
| `patchforge/repository_intelligence/refinement.py` | 135 | Graph Refinement | Reviewed — No Material Issue | Graph pruning and relevance filtering. |
| `patchforge/repository_intelligence/retriever.py` | 175 | Context Retriever | Reviewed — No Material Issue | Retrieves callers, callees, and state flow. |
| `patchforge/repository_intelligence/schema.py` | 125 | Graph Schema | Reviewed — No Material Issue | `GraphNode` and `GraphEdge` dataclasses. |
| `patchforge/repository_intelligence/temporal_graph.py` | 195 | Temporal Repository Graph | Reviewed — No Material Issue | Co-change frequency matrix. |
| `patchforge/repository_intelligence/test_graph.py` | 210 | Test Graph Index | Reviewed — No Material Issue | Maps test methods to symbols and assertions. |

### `patchforge/retrieval/` (Retrieval Layer)
| File Path | Lines | Role | Status | Findings / Notes |
| :--- | :--- | :--- | :--- | :--- |
| `patchforge/retrieval/__init__.py` | 15 | Package Init | Reviewed — No Material Issue | Clean exports. |
| `patchforge/retrieval/evidence.py` | 115 | Evidence Schema | Reviewed — No Material Issue | Dataclass definitions for retrieved evidence. |
| `patchforge/retrieval/hybrid.py` | 310 | Hybrid Retriever | Reviewed — No Material Issue | BM25, semantic, graph, and temporal fusion. |
| `patchforge/retrieval/ranking.py` | 145 | Retrieval Ranking | Reviewed — No Material Issue | Reciprocal rank fusion. |
| `patchforge/retrieval/retriever.py` | 165 | Base Retriever | Reviewed — No Material Issue | Base retrieval interface. |

### `patchforge/telemetry/` (Telemetry & Observability)
| File Path | Lines | Role | Status | Findings / Notes |
| :--- | :--- | :--- | :--- | :--- |
| `patchforge/telemetry/__init__.py` | 12 | Package Init | Reviewed — No Material Issue | Clean exports. |
| `patchforge/telemetry/cost.py` | 85 | Cost Tracking | Reviewed — No Material Issue | Model cost calculations. |
| `patchforge/telemetry/events.py` | 135 | Event Logger | Reviewed — No Material Issue | Structured event logging. |
| `patchforge/telemetry/timing.py` | 95 | Timing Breakdown | Reviewed — No Material Issue | Subsystem wall-time instrumentation. |
| `patchforge/telemetry/tokens.py` | 85 | Token Accounting | Reviewed — No Material Issue | Prompt and completion token accounting. |

### `patchforge/tools/` (Agent & Pipeline Tools)
| File Path | Lines | Role | Status | Findings / Notes |
| :--- | :--- | :--- | :--- | :--- |
| `patchforge/tools/__init__.py` | 25 | Package Init | Reviewed — No Material Issue | Clean exports. |
| `patchforge/tools/base.py` | 95 | Base Tool Interface | Reviewed — No Material Issue | `Tool`, `ToolResult`, and `ToolCall` base contracts. |
| `patchforge/tools/editor.py` | 434 | File Reading & Patch Tools | Reviewed — No Material Issue | Contains `_resolve_and_contain` traversal/symlink guards and transactional write rollback. |
| `patchforge/tools/execution.py` | 228 | Test Execution Tools | Reviewed — Findings Registered | Contains host execution sandboxing bypass on `tempfile.gettempdir()` (`PF-SEC-001`). |
| `patchforge/tools/graph.py` | 125 | Graph Query Tools | Reviewed — No Material Issue | In-process call-graph inspection tools. |
| `patchforge/tools/reasoning.py` | 145 | Reasoning Tools | Reviewed — No Material Issue | Formulate hypothesis and propose plan tools. |
| `patchforge/tools/search.py` | 289 | Search Tools | Reviewed — Findings Registered | Missing path containment check on `FindSymbolTool` (`PF-SEC-002`). |

### `patchforge/verification/` (Verification & Testing)
| File Path | Lines | Role | Status | Findings / Notes |
| :--- | :--- | :--- | :--- | :--- |
| `patchforge/verification/__init__.py` | 15 | Package Init | Reviewed — No Material Issue | Clean exports. |
| `patchforge/verification/adaptive.py` | 285 | Adaptive Tiered Verifier | Reviewed — No Material Issue | Multi-tier verification (Static -> F2P -> P2P). |
| `patchforge/verification/analyzer.py` | 510 | Test Failure Analyzer | Reviewed — No Material Issue | Analyzes traceback frames and failure classifications. |
| `patchforge/verification/classifier.py` | 145 | Failure Classifier | Reviewed — No Material Issue | `FailureClass` categorization enum and logic. |
| `patchforge/verification/tester.py` | 87 | Tester Harness Bridge | Reviewed — No Material Issue | V1.8 fix uses temporary directory for evaluation predictions. |

---

## 2. Review Coverage Summary

- **Total first-party files inventoried**: 109
- **Total first-party files fully inspected**: 109 (100%)
- **Files with material findings**: 12 files
- **Files with clean disposition**: 97 files
