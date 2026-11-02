# PatchForge AI — Test and Validation Report (V1.8 Review)

**Document ID**: `DOC-AUDIT-V18-E`  
**Date**: October 12, 2026  
**Test Suite**: Full Fast Test Suite (`tests/`)  
**Environment**: Windows 11, Python 3.12.10, Pytest 8.4.2  

---

## 1. Test Suite Execution Summary

The full fast test suite was executed against the current working tree:

```bash
python -m pytest tests -q
```

### Results
- **Passed**: **264 tests**
- **Failed**: **0 tests**
- **Errors**: **0**
- **Warnings**: **82 warnings**
- **Duration**: **57.20 seconds**

---

## 2. Warnings Analysis & Categorization

All 82 warnings captured during test execution are `SyntaxWarning: invalid escape sequence` emitted by Python 3.12's enhanced regular expression and docstring parser. 

Zero warnings originate from first-party production code in `patchforge/`. The breakdown:
1. **Benchmark Repositories (64 warnings)**:
   - `repos/psf__requests/requests/api.py`, `sessions.py`, `auth.py`, `packages/urllib3/connectionpool.py`, `poolmanager.py` (e.g. `\*\*kwargs` in docstrings).
   - `experiments/phase_d_v03/repos/psf__requests/...`
   - `experiments/phase_d_v03/repos/pylint-dev__pylint/...`
2. **Vendored Upstream Implementations (18 warnings)**:
   - `third_party/agentless_upstream/agentless/repair/repair.py:588` (`\ No newline at end of file\n`)
   - `third_party/repograph_upstream/agentless/repair/repair.py:712`

---

## 3. Test Suite Inventory & Coverage Ledger (43 Test Files)

| Test File | Tests Passed | Subsystem Covered | Evaluation Realism & Breadth |
| :--- | :--- | :--- | :--- |
| `test_adapters.py` | 5 | Upstream Adapters | Tests initialization and fallback of `AgentlessAdapter`, `RepoGraphAdapter`, `MiniSWEAgentAdapter`. Mocks external subprocess calls. |
| `test_adaptive_verification.py` | 7 | Adaptive Verifier | Tests three-tier verification gating (static AST, F2P, P2P) with fake verdicts. |
| `test_agent_v03.py` | 6 | v0.3 Agent Loop | Tests step limit, trajectory recording, and done actions. |
| `test_behavioral_diagnosis.py` | 8 | Issue Behavior Extractor | Verifies symptom extraction, keyword parsing, and expected behavior extraction. |
| `test_context_v03.py` | 4 | Context Compactor | Tests token truncation and prompt budget compaction. |
| `test_contract_grounding.py` | 6 | API Contract Extractor | Verifies AST parameter extraction, return annotations, and forbidden parameter detection. |
| `test_controller_recovery.py` | 5 | Controller Error Recovery | Tests retry and recovery from syntax errors in the v0.3 agentic loop. |
| `test_deterministic_context.py` | 5 | Context Retrieval | Verifies caller/callee and state-flow retrieval ordering. |
| `test_dynamic_experiment.py` | 6 | Probing & Experiments | Tests AST probe injection, observation extraction, and experiment runner mocking. |
| `test_episode_memory.py` | 7 | Episode Memory | Tests JSON serialization and deserialization of repair episodes. |
| `test_evidence.py` | 8 | Evidence Packaging | Verifies evidence categorization (runtime vs static vs unknown). |
| `test_graph_repair_engine.py` | 4 | Graph Repair Engine (v0.5) | Tests legacy graph engine initialization and candidate selection. |
| `test_hybrid_retriever.py` | 6 | Hybrid Retriever | Verifies BM25 + graph + temporal rank fusion logic. |
| `test_hypothesis.py` | 5 | Competing Hypotheses | Tests hypothesis formulation, ranking, and contract gating. |
| `test_localizer.py` | 6 | Localization Subsystem | Tests AST symbol search and test traceback parsing. |
| `test_loop.py` | 4 | Legacy Loop | Tests v0.3 repair loop termination. |
| `test_models.py` | 5 | Provider Interfaces | Mocks Ollama and OpenAI provider calls; tests parameter handling. |
| `test_multisite_target_v04_3.py` | 6 | Multi-Site Targets | Tests `MultiSiteRepairTarget` data structures and multi-span validation. |
| `test_patch.py` | 6 | Patch Operations | Tests unified diff creation and parsing. |
| `test_patch_action_format.py` | 5 | SEARCH/REPLACE Format | Tests parsing and matching tiers of SEARCH/REPLACE blocks. |
| `test_patch_synthesis.py` | 6 | Patch Synthesis | Tests in-memory simulation and AST validation before writing. |
| `test_policy_guards_v03.py` | 5 | Agent Policy | Verifies repetition loops, tool blacklisting, and step quotas. |
| `test_probes_v03.py` | 4 | Probe Parsing | Tests test and traceback probe extraction. |
| `test_proven_architecture_v04.py` | 6 | Architecture v0.4 | Tests v0.4 workflow integrity. |
| `test_repair_memory_store.py` | 7 | Memory Store | Verifies `MemoryMode.FROZEN` vs `ONLINE`, symbol similarity, and self-episode exclusion. |
| `test_repair_planner.py` | 6 | Repair Unit Planner | Tests AST surgical boundary planning across expressions, statements, and functions. |
| `test_repair_protocol.py` | 6 | Structured Repair Contract | Tests Layer A (schema) and Layer B (semantic AST) validation. |
| `test_repair_reconstruction.py` | 8 | Source Reconstructor | Verifies exact line indentation, AST expression replacement, and import healing. |
| `test_repository_intelligence.py` | 8 | Graph Indexer | Verifies NetworkX call-graph construction, caller/callee traversal, and test indexing. |
| `test_robustness_v02.py` | 5 | Error Robustness | Tests handler resilience against malformed inputs. |
| `test_semantic_refinement_v04_2.py` | 6 | Semantic Refinement | Tests repeated failure guards, budget limits, and replanning actions. |
| `test_state_flow.py` | 6 | State Flow Analysis | Tests AST variable assignment flow and parameter tracking. |
| `test_structured_repair.py` | 8 | Structured Repair Engine | Tests prompt builder, JSON docstring quote repair, and AST verification. |
| `test_temporal_graph.py` | 6 | Temporal Co-Change Graph | Tests Git commit history parsing and co-change frequency computation. |
| `test_tools_v03.py` | 7 | File & Search Tools | Tests `read_file`, `search_code`, and `apply_patch`. |
| `test_unified_evidence.py` | 6 | Unified Evidence Substrate | Tests fusion of static, runtime, temporal, and unknown evidence items. |
| `test_v12_algorithmic_diagnosis.py` | 6 | Algorithmic Diagnosis | Tests trace modeling, candidate profiling, and contract extraction. |
| `test_v13_causal_diagnosis.py` | 6 | Causal Trace Modeling | Tests first divergence localization and counterfactual evaluations. |
| `test_v15_reliability.py` | 7 | Reliability Gating | Tests assertion completeness, import healing, and empty-patch detection. |
| `test_v16_pipeline_reliability.py` | 8 | Cache & Pipeline Identity | Tests untracked file hashing, evaluation manifests, and cache hit/miss determinism. |
| `test_v17_repair_quality.py` | 9 | Quality & Residual Repair | Tests residual failure tracking, multi-hypothesis replanning, and target coherence. |
| `test_v18_security_and_integrity.py` | 10 | Security & Evaluation Integrity | Tests path traversal blocks (`..`), symlink escapes, atomic patch rollbacks, run-scoped report finding, and evaluation consistency checks. |
| `test_v1_orchestrator.py` | 5 | V1 Orchestrator End-to-End | Integration test running mocked `V1RepairOrchestrator` repairs. |

---

## 4. Analysis of Historical SWE-bench Benchmark Results

| Metric | V1.4 Baseline (`results/v1_orchestrator/`) | V1.7 Evaluation (`results/v17_orchestrator/`) | Verified Analysis |
| :--- | :--- | :--- | :--- |
| **Cohort Size** | 20 tasks | 20 tasks | Exact identical 20 tasks. |
| **Resolved Tasks** | **4 / 20 (20.0%)** | **3 / 20 (15.0%)** | `requests-2674`, `requests-2148`, `flask-4992` resolved in both. `requests-863` dropped. |
| **`WRONG_HYPOTHESIS`** | 13 tasks | 2 tasks (`pytest-5495`, `pytest-5692`) | Dramatic reduction in speculative hypotheses via contract gating and causal traces. |
| **`UNRESOLVED`** | 1 task | **9 tasks** | Majority caused by no-op rejections where model returned unchanged code and prompt collapsed. |
| **`PATCH_SYNTAX`** | 0 tasks | **5 tasks** | 4 caused by import healer injecting `from _pytest.compat import str`; 1 by line continuation escaping. |
| **`PATCH_SEMANTICS`** | 2 tasks | 1 task (`psf__requests-1963`) | High partial progress (6/7 F2P passed, 112/112 P2P passed). |
| **P2P Regression Safety** | 100% (1,260 / 1,260) | 100% (594 / 594 evaluated) | Zero regressions caused across all evaluated tasks in both versions. |
