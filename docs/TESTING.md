# PatchForge AI — Testing & Verification Guide

PatchForge AI maintains a comprehensive, deterministic test suite designed to run quickly without requiring live LLM calls, Docker daemons, or network access during ordinary CI runs.

---

## 1. Test Suite Organization

The test suite is organized under `tests/` into distinct layers:

```
tests/
├── conftest.py                             # Global pytest fixtures & Windows Git safe.directory setup
├── test_adapters.py                        # SWE-bench & MiniSWEAgent tool execution mocks
├── test_adaptive_verification.py           # Multi-tier verification & replanning logic
├── test_agent_v03.py                       # V0.3 AgentController loop tests
├── test_behavioral_diagnosis.py            # Issue behavior & contract extraction tests
├── test_context_v03.py                     # ContextCompactor token reduction tests
├── test_contract_grounding.py              # API contract extraction & parameter validation
├── test_controller_recovery.py             # AgentController error handling & tool recovery
├── test_deterministic_context.py           # Context retrieval determinism & order stability
├── test_dynamic_experiment.py              # Probe injection & observation tests
├── test_episode_memory.py                  # Episode storage & history tracking
├── test_evidence.py                        # EvidencePackage collation tests
├── test_graph_repair_engine.py             # GraphRepairEngine traversal tests
├── test_hybrid_retriever.py                # Hybrid lexical/AST/test retrieval scoring
├── test_hypothesis.py                      # Hypothesis formulation & confidence tests
├── test_localizer.py                       # Candidate localization tests
├── test_loop.py                            # RepairLoop state transition tests
├── test_models.py                          # Ollama & OpenRouter provider mocking
├── test_multisite_target_v04_3.py          # Multi-site edit target qualification
├── test_patch.py                           # Patch data structure & unidiff generation
├── test_patch_action_format.py             # RepairAction JSON schema validation
├── test_patch_synthesis.py                 # Structured repair prompt formatting
├── test_policy_guards_v03.py               # Tool budget & loop prevention policy tests
├── test_probes_v03.py                      # Dynamic probe generation tests
├── test_proven_architecture_v04.py         # Architectural invariant assertions
├── test_repair_memory_store.py             # RepairMemoryStore attempt deduplication
├── test_repair_planner.py                  # ASTSpanVisitor boundary mapping
├── test_repair_protocol.py                 # Prompt and response schema protocols
├── test_repair_reconstruction.py           # SourceReconstructor AST and line matching
├── test_repository_intelligence.py         # SymbolExtractor & RepositoryGraph tests
├── test_robustness_v02.py                  # BaselineRepairEngine edge case handling
├── test_semantic_refinement_v04_2.py       # Replanning upon partial F2P pass
├── test_state_flow.py                      # CFGBlock & variable transition modeling
├── test_structured_repair.py               # StructuredRepairPromptBuilder & Parser
├── test_temporal_graph.py                  # TemporalRepositoryGraph commit churn tests
├── test_tools_v03.py                       # Sandboxed editor, search, and exec tools
├── test_unified_evidence.py                # UnifiedEvidenceEngine integration
├── test_v12_algorithmic_diagnosis.py       # V1.2 algorithmic diagnosis tests
├── test_v13_causal_diagnosis.py            # V1.3 causal trace diagnosis tests
├── test_v15_reliability.py                 # V1.5 assertion completeness tests
├── test_v16_pipeline_reliability.py        # V1.6 pipeline stability & error resilience
├── test_v17_repair_quality.py              # V1.7 repair synthesis quality tests
├── test_v18_batch_correctness.py           # V1.8 batch evaluation verification
├── test_v18_security_and_integrity.py      # V1.8 security, path containment & run isolation
├── test_v19_phase1_contract_gate.py        # V1.9 Contract Gate selection policy
├── test_v19_phase2_hypothesis_consistency.py # V1.9 Hypothesis index & rank consistency
├── test_v19_phase3_classifier_and_replanner.py # V1.9 Classifier & replanner healing
├── test_v19_phase4_candidate_identity_and_ranking.py # V1.9 Qualified target identity
├── test_v19_phase5_external_integrations.py # V1.9 Work item, GitHub webhook & direct API
├── test_v19_phase6_pipeline_compat.py      # V1.9 End-to-end pipeline wiring
└── test_v1_orchestrator.py                 # Canonical V1RepairOrchestrator lifecycle
```

---

## 2. Running Offline Tests

The complete offline test suite (332 tests) runs in ~25 to 40 seconds:

```bash
# Run all tests quietly
python -m pytest tests -q

# Run with verbose output
python -m pytest tests -v

# Run a specific test module
python -m pytest tests/test_v19_phase1_contract_gate.py -v

# Run with stop-on-first-failure
python -m pytest tests -x
```

### Expected Baseline Result:
```text
332 passed, 82 warnings in 42.55s
```
*(Warnings are harmless Python 3.12 syntax escape warnings in third-party cloned repositories).*

---

## 3. Mock Boundaries vs. Live Executions

To ensure tests run deterministically and safely in any environment without external services:

| Component | Offline Test Mode (Pytest) | Live Production Mode |
|:---|:---|:---|
| **LLM Inference** | Mocked with fixed JSON strings (`test_models.py`, `test_structured_repair.py`) | Connects via HTTP to `OllamaProvider` (`http://localhost:11434`) |
| **Docker Engine** | Mocked via `unittest.mock.patch` returning synthesized stdout/exit codes | Mounts and executes real SWE-bench Docker containers via subprocess |
| **Git Operations** | Operates on temporary ephemeral git repos created in `tempfile.TemporaryDirectory` | Operates on benchmark task checkouts in `workspaces/` |
| **GitHub Webhooks** | Synthesizes binary payloads and signs them with local HMAC keys | Receives live HTTP payloads from GitHub webhook deliveries |

---

## 4. Running Benchmark Evaluations

To execute real SWE-bench benchmark evaluations with live LLM and Docker:

```bash
# 1. Ensure Ollama daemon is running with model loaded
ollama serve &
ollama pull qwen2.5-coder:14b

# 2. Ensure Docker Engine is active
docker ps

# 3. Run a targeted task evaluation
python scripts/run_v05_cohort.py --instances pallets__flask-4992 --mode v1 --model qwen2.5-coder:14b

# 4. Run the full frozen 20-task benchmark cohort
python scripts/run_v05_cohort.py --mode v1 --model qwen2.5-coder:14b
```

Results are saved to `results/v19_orchestrator/<instance_id>.json` and summarized in `results/v19_orchestrator/summary.json`.
