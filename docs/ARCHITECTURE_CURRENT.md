# PatchForge AI — Current Architecture Audit (V0.4.2)

**Date**: 2026-09-19  
**Status**: Authoritative Reference  
**Auditor**: Principal Autonomous Engineer  

---

## 1. High-Level Architecture Overview

PatchForge currently has two distinct operating paradigms:
1. **V0.4+ Proven Baseline Pipeline (`patchforge/pipeline/baseline.py`)**:
   A deterministic, single-turn + bounded evidence-driven refinement engine inspired by Agentless and Aider. This is the **only** pipeline that has achieved test-verified benchmark resolutions on SWE-bench (`psf__requests-863`).
2. **V0.3 Multi-Turn Agentic Subsystem (`patchforge/agent/controller.py`, `patchforge/core/orchestrator_v03.py`)**:
   A multi-turn tool-calling agent with policy guards, context compaction, and 13 typed tools. While architecturally modular, this subsystem historically suffered from turn exhaustion, tool loop oscillations, and format translation overhead.

```text
CURRENT CANONICAL REPAIR FLOW (V0.4.2):
========================================================================
[SWE-bench Problem / GitHub Issue]
   │
   ▼
[1. Stem-Normalized Localization] (patchforge/pipeline/baseline.py)
   │  - Lexical keyword extraction + symbol scoring
   │  - Plural/singular stem matching (_normalize_stem)
   ▼
[2. Authoritative RepairTarget] (patchforge/core/target.py)
   │  - Surgical unit extraction (patchforge/repository/map.py)
   │  - function -> method -> small class region -> file window
   │  - Verified source code with exact byte boundaries
   ▼
[3. Surgical Context Construction] (Aider-style compact context)
   │  - Issue description (bounded to 1500 chars)
   │  - Repair target & verified source (verbatim, zero line numbers)
   │  - Related failing test signatures
   ▼
[4. Model Patch Synthesis] (OllamaProvider: qwen2.5-coder:7b)
   │  - System prompt enforcing strict apply_patch JSON action
   │  - Exact SEARCH/REPLACE block specification
   ▼
[5. Grounded Patch Verification & Apply] (patchforge/tools/editor.py)
   │  - SEARCH block verbatim matching
   │  - Whitespace tolerance diagnostics (SEARCH_WHITESPACE_MISMATCH)
   │  - In-memory AST syntax validation (validate_ast)
   │  - Patch size anomaly detection
   ▼
[6. SWE-bench Docker Harness Evaluation] (patchforge/integrations/swebench.py)
   │  - Isolated Docker container execution
   │  - FAIL_TO_PASS and PASS_TO_PASS test execution
   │  - Structured EvalResult with stdout/stderr/test_output
   ▼
[7. Verdict & Failure Classification] (patchforge/verification/classifier.py)
   │  - RESOLVED / TEST_FAILURE / REGRESSION / INFRA_FAILURE / PATCH_SYNTAX
   │
   ├─► IF RESOLVED: Terminate (100% Success)
   │
   └─► IF UNRESOLVED & NON-INFRA:
         ▼
       [8. ExecutionEvidence Extraction] (patchforge/retrieval/evidence.py)
         │  - Target/regression pass/fail counts
         │  - Full traceback & stdout/stderr captures
         ▼
       [9. Deterministic FailureAnalyzer] (patchforge/verification/analyzer.py)
         │  - Test failure details (AssertionError, diffs, loci)
         │  - Regression clustering ((error_type, file, line))
         │  - SemanticDiagnosis synthesis
         ▼
       [10. Bounded Refinement Loop] (Max 2 cycles)
         │  - RefinedHypothesis formulation
         │  - Repeated identical failure guard (REFINEMENT_EXHAUSTED)
         │  - Regression safety guard (REGRESSION_INTRODUCED)
         │  - Candidate patch selection (Pareto optimal test pass)
```

---

## 2. Module-by-Module Inventory

| Module | Core Classes / Functions | Operational Status | Quality & Role |
| :--- | :--- | :---: | :--- |
| `patchforge/pipeline/baseline.py` | `BaselineRepairEngine`, `BaselineResult` | **ACTIVE** | **Primary repair engine**. Implements stem-normalized localization, surgical context, patch generation, and bounded refinement. |
| `patchforge/core/target.py` | `RepairTarget` | **ACTIVE** | **Authoritative target model**. Defines single-site file, symbol, span, verified source. (Needs multi-site extension). |
| `patchforge/repository/map.py` | `RepoMap`, `SymbolVisitor` | **ACTIVE** | **Repository intelligence**. AST-based symbol extraction, class/method hierarchy, surgical unit extraction. |
| `patchforge/verification/analyzer.py` | `FailureAnalyzer`, `RegressionCluster`, `SemanticDiagnosis` | **ACTIVE** | **Deterministic diagnostics**. Parses pytest traces, clusters regressions, formats compact prompt blocks. |
| `patchforge/verification/classifier.py` | `FailureClass`, `classify()` | **ACTIVE** | **Taxonomy enforcement**. Classifies patch outcomes into 12 standard failure classes. |
| `patchforge/verification/tester.py` | `Tester` | **ACTIVE** | **Test bridge**. Dispatches patches to `SWEBenchAdapter`. |
| `patchforge/retrieval/evidence.py` | `ExecutionEvidence`, `EvidenceType` | **ACTIVE** | **Execution feedback container**. Standard typed model for runtime evidence. |
| `patchforge/tools/editor.py` | `ApplyPatchTool`, `ViewFileTool`, `ReadTestTool` | **ACTIVE** | **Deterministic patch validation**. Emits unified diffs, enforces verbatim SEARCH matches, AST validation. |
| `patchforge/models/provider.py` | `OllamaProvider`, `OpenAIProvider`, `LLMResponse` | **ACTIVE** | **Model abstraction**. Handles local Ollama (qwen2.5-coder:7b) and OpenAI-compatible endpoints. |
| `patchforge/integrations/swebench.py` | `SWEBenchAdapter`, `EvalResult` | **ACTIVE** | **Benchmark interface**. Manages SWE-bench Docker evaluation, report parsing, and container lifecycle. |
| `patchforge/telemetry/events.py` | `TelemetryLogger`, `TelemetryEvent` | **ACTIVE** | **Telemetry**. Tracks wall clock, LLM token counts, test duration, and phase transitions. |
| `patchforge/agent/controller.py` | `AgentController` | DORMANT (V0.3) | Multi-turn agent loop. Working but bypassed by V0.4 baseline mode. |
| `patchforge/agent/policy.py` | `AgentPolicy`, guards | DORMANT (V0.3) | Action guards for agent mode. |
| `patchforge/agent/context.py` | `ContextCompactor` | DORMANT (V0.3) | Dynamic token compactor for multi-turn conversations. |
| `patchforge/core/orchestrator.py` | `Orchestrator` | LEGACY | V0.1 waterfall orchestrator. Superceded by baseline pipeline. |
| `patchforge/core/orchestrator_v03.py`| `OrchestratorV03` | LEGACY | V0.3 waterfall orchestrator. Superceded by baseline pipeline. |
| `patchforge/reasoning/probes.py` | `ExecutionProbeManager` | UNUSED | Code injection probes. Not utilized in current baseline. |
| `patchforge/reasoning/ranker.py` | `HypothesisRanker` | UNUSED | Hypothesis ranker. Replaced by direct hypothesis generation. |
| `patchforge/reasoning/specification.py`| `SpecificationExtractor` | UNUSED | Behavioral specification module. |
| `patchforge/memory/episode.py` | `EpisodeMemory`, `RepairEpisode`| UNUSED | Repair memory storage. Schema present but not actively queried. |
| `patchforge/tools/graph.py` | `RepoGraphTool` | UNUSED | Graph tool for agent mode. |
| `patchforge/tools/reasoning.py` | `FormulateHypothesisTool` | UNUSED | Agent-mode hypothesis tool. |

---

## 3. Verified Benchmark Baseline (V0.4.2)

- **psf__requests-863**: RESOLVED (4/4 FAIL_TO_PASS, 60/60 PASS_TO_PASS, 148.49s, 0 refinements)
- **pallets__flask-4045**: REFINEMENT_EXHAUSTED (1/2 FAIL_TO_PASS, 50/50 PASS_TO_PASS, 118.77s, 1 refinement halted by repeated failure guard)
- **psf__requests-1963**: REFINEMENT_EXHAUSTED (5/7 FAIL_TO_PASS, 112/112 PASS_TO_PASS, 422.92s, 2 refinements halted by budget cap)
- **psf__requests-2674**: REGRESSION (12/12 FAIL_TO_PASS, 132/142 PASS_TO_PASS, 204.26s, 1 refinement halted by verbatim search guard)
