# PatchForge AI — Comprehensive Module Reference

This document provides a module-by-module reference of the PatchForge AI codebase (`patchforge/`).

---

## 1. Pipeline (`patchforge/pipeline/`)

### `v1_orchestrator.py` (CANONICAL)
- **Responsibility**: End-to-end orchestration of the V1.9 repair loop: coordinates indexing, retrieval, hypothesis generation, contract gating, candidate ranking, structured repair, AST reconstruction, Docker testing, failure classification, and replanning.
- **Key Classes**:
  - `V1RepairOrchestrator`: The central production repair orchestrator.
  - `V1OrchestratorResult`: Data container for trajectory, patch diff, execution evidence, timings, and resolution status.
- **Key Methods**:
  - `run_task(instance_id, repo_dir, problem_statement, ...)`: Executes the full repair lifecycle for a single task.
  - `_execute_repair_attempt(...)`: Manages a single cycle of LLM prompting, reconstruction, Docker testing, and classification.
- **Inputs**: Task instance ID, repository workspace path, problem statement text, test specification.
- **Outputs**: `V1OrchestratorResult` with generated unified git diff, test counts, and artifact records.
- **Dependencies**: `patchforge.diagnosis`, `patchforge.repair`, `patchforge.repository_intelligence`, `patchforge.retrieval`, `patchforge.verification`, `patchforge.integrations.swebench`.
- **Tests**: `tests/test_v1_orchestrator.py`, `tests/test_v16_pipeline_reliability.py`, `tests/test_v17_repair_quality.py`, `tests/test_v19_phase6_pipeline_compat.py`.

### `baseline.py` (ACTIVE SUPPORT)
- **Responsibility**: Provides comparative baseline repair capability (`BaselineRepairEngine`) that bypasses graph intelligence and contract gating to measure raw LLM repair efficacy.
- **Key Classes**: `BaselineRepairEngine`.
- **Tests**: `tests/test_robustness_v02.py`, `tests/test_semantic_refinement_v04_2.py`.

### `graph_engine.py` (ACTIVE SUPPORT / EXPERIMENTAL)
- **Responsibility**: Alternative repair pipeline driven directly by code-graph reachability traversal.
- **Key Classes**: `GraphRepairEngine`, `GraphEngineResult`.
- **Tests**: `tests/test_graph_repair_engine.py`.

---

## 2. Diagnosis (`patchforge/diagnosis/`)

### `diagnosis.py` (CANONICAL)
- **Responsibility**: Formulates competing root-cause hypotheses based on issue behavior, failure tracebacks, and retrieved code context.
- **Key Classes**:
  - `DiagnosisHypothesis`: Represents an individual diagnosis hypothesis with causal predictions, target files/functions, and confidence score.
  - `CompetingDiagnosisResult`: Holds the ranked list of competing hypotheses, the selected primary hypothesis, and diagnosis metadata.
- **Key Methods**:
  - `formulate_hypotheses(...)`: Generates and ranks hypotheses. Maintains consistent hypothesis index and rank alignment.
- **Tests**: `tests/test_hypothesis.py`, `tests/test_v19_phase2_hypothesis_consistency.py`.

### `contract_gate.py` (CANONICAL)
- **Responsibility**: Evaluates behavioral requirements and counterfactual constraints across candidate hypotheses.
- **Key Classes**:
  - `HypothesisContractGate`: Enforces the selection policy. In V1.9, preserves the Primary Diagnosis hypothesis unless counterfactual score > 0.8 and confidence requirements are met.
  - `BehavioralRequirementMatrix`: Tabulates requirements vs. hypothesis satisfaction.
- **Key Methods**:
  - `evaluate_hypotheses(...)`: Scores hypotheses and selects the safest, most grounded candidate.
- **Tests**: `tests/test_contract_grounding.py`, `tests/test_v19_phase1_contract_gate.py`.

### `contract.py` (CANONICAL)
- **Responsibility**: Extracts API contracts, parameter requirements, return type invariants, and exception specifications.
- **Key Classes**: `APIContract`, `APIParameter`, `ContractCategory`.

### `behavior.py` (CANONICAL)
- **Responsibility**: Extracts structured behavioral requirements (expected vs actual behavior, exception types, repro steps) from raw issue descriptions.
- **Key Classes**: `IssueBehaviorExtractor`, `IssueBehaviorMap`.
- **Tests**: `tests/test_behavioral_diagnosis.py`.

### `candidate_analysis.py` (CANONICAL)
- **Responsibility**: Analyzes candidate edit site blast radius, caller/callee relationships, and helper function classifications.
- **Key Classes**: `CandidateBlastRadius`, `HelperClassification`, `CausalPath`.

### `causal_trace.py` (CANONICAL)
- **Responsibility**: Models causal execution paths from defect trigger to observed failure symptom.
- **Key Classes**: `BehavioralTrace`, `TraceEvent`, `TraceEventType`.
- **Tests**: `tests/test_v13_causal_diagnosis.py`.

### `evidence.py` (CANONICAL)
- **Responsibility**: Packages static and dynamic evidence items into structured packages.
- **Key Classes**: `EvidencePackage`, `EvidenceItem`, `EvidenceKind`.
- **Tests**: `tests/test_evidence.py`.

---

## 3. Repair (`patchforge/repair/`)

### `ranking.py` (CANONICAL)
- **Responsibility**: Ranks candidate repair sites using fully qualified identities (`file::class::function::action_target`) and multi-factor scoring (AST relevance, hypothesis alignment, failure proximity).
- **Key Classes**: `RepairSiteRanker`.
- **Key Methods**:
  - `rank_sites(candidates, hypothesis, ...)`: Produces deterministic sorted list of `RepairTarget` objects with qualified profile keys.
- **Tests**: `tests/test_v19_phase4_candidate_identity_and_ranking.py`.

### `structured_repair.py` (CANONICAL)
- **Responsibility**: Formulates targeted repair prompts requiring strict JSON responses adhering to the `RepairAction` schema and parses the model's raw string output.
- **Key Classes**: `StructuredRepairPromptBuilder`, `StructuredRepairParser`.
- **Key Methods**:
  - `build_prompt(...)`: Injects bounded code context, API contract, and hypothesis explanation into the prompt.
  - `parse_response(raw_text)`: Strips conversational framing and markdown blocks to extract valid JSON action arrays.
- **Tests**: `tests/test_structured_repair.py`.

### `reconstructor.py` (CANONICAL)
- **Responsibility**: Reconstructs source files by applying surgical `RepairAction` edits (`REPLACE_BLOCK`, `INSERT_BEFORE`, `INSERT_AFTER`, `WRAP_TRY_EXCEPT`) using AST span matching with robust whitespace-tolerant line fallbacks.
- **Key Classes**: `SourceReconstructor`.
- **Key Methods**:
  - `apply_actions(source_code, actions)`: Returns modified source code preserving comments and unaffected formatting.
- **Tests**: `tests/test_repair_reconstruction.py`.

### `replanner.py` (CANONICAL)
- **Responsibility**: Evaluates test failure classifications and decides whether to accept, retry with a refined prompt, switch target site, or switch diagnosis hypothesis.
- **Key Classes**: `FailureDrivenReplanner`, `ReplanDecision`, `ReplanAction`.
- **Key Methods**:
  - `replan(...)`: Returns `ReplanDecision` with formatted reason string documenting F2P pass counts.
- **Tests**: `tests/test_v19_phase3_classifier_and_replanner.py`, `tests/test_adaptive_verification.py`.

### `schema.py` (CANONICAL)
- **Responsibility**: Defines the repair unit types and validates JSON structures against the `RepairAction` schema.
- **Key Classes**: `RepairUnitType`, `RepairAction`, `SchemaValidationResult`.

### `validator.py` (CANONICAL)
- **Responsibility**: Static validation of reconstructed source code using `ast.parse` to ensure patches are syntactically valid before launching Docker containers.
- **Key Classes**: `StaticRepairValidator`.

### `planner.py` (CANONICAL)
- **Responsibility**: Maps function and block boundaries into AST span ranges.
- **Key Classes**: `RepairUnitPlanner`, `ASTSpanVisitor`.
- **Tests**: `tests/test_repair_planner.py`.

### `residual.py` (ACTIVE SUPPORT)
- **Responsibility**: Tracks residual test failures (newly passing tests vs remaining failures) across successive iterations.
- **Key Classes**: `ResidualFailureRecord`.

---

## 4. Verification (`patchforge/verification/`)

### `classifier.py` (CANONICAL)
- **Responsibility**: Classifies test harness outcomes based on F2P/P2P pass counts, tracebacks, exit codes, and syntax errors.
- **Key Classes**: `FailureClass` (Enum: `ALL_PASSED`, `FAIL_TO_PASS_FAILURE`, `PASS_TO_PASS_REGRESSION`, `SYNTAX_ERROR`, `EXECUTION_TIMEOUT`, `WRONG_HYPOTHESIS`, `INCOMPLETE_REPAIR`).
- **Key Methods**:
  - `classify(...)`: Evaluates execution evidence. Treats empty tracebacks with 0 F2P passed as `WRONG_HYPOTHESIS`.
- **Tests**: `tests/test_loop.py`, `tests/test_v19_phase3_classifier_and_replanner.py`.

### `tester.py` (CANONICAL)
- **Responsibility**: Executes local pytest runs or delegates to containerized Docker harnesses with strict timeouts.
- **Key Classes**: `Tester`, `TestReport`.

### `adaptive.py` (ACTIVE SUPPORT)
- **Responsibility**: Multi-tier adaptive verification (Tier 1: static check; Tier 2: targeted test; Tier 3: full regression suite).
- **Key Classes**: `AdaptiveVerifier`, `VerificationTier`, `AdaptiveVerificationVerdict`.
- **Tests**: `tests/test_adaptive_verification.py`.

---

## 5. Repository Intelligence (`patchforge/repository_intelligence/`)

### `indexer.py` (CANONICAL)
- **Responsibility**: Traverses target repositories, parses Python files into ASTs, and indexes classes, functions, docstrings, and imports.
- **Key Classes**: `RepositoryIndexer`.

### `parser.py` (CANONICAL)
- **Responsibility**: AST symbol extractor capturing definition spans, parameter lists, return types, and docstrings.
- **Key Classes**: `SymbolExtractor`.

### `graph.py` (CANONICAL)
- **Responsibility**: Constructs and queries directed repository code graphs (call graph, inheritance graph, import graph) using `networkx`.
- **Key Classes**: `RepositoryGraph`.
- **Tests**: `tests/test_repository_intelligence.py`.

### `test_graph.py` (CANONICAL)
- **Responsibility**: Parses test files, extracts individual test cases and assertions, and links them to tested production symbols.
- **Key Classes**: `TestExtractor`, `TestCaseEntity`, `TestAssertion`.

### `temporal_graph.py` (CANONICAL)
- **Responsibility**: Extracts git commit churn history to identify frequently modified, high-risk symbols.
- **Key Classes**: `TemporalRepositoryGraph`, `SymbolTemporalProfile`, `CommitRecord`.
- **Tests**: `tests/test_temporal_graph.py`.

### `flow.py` (CANONICAL)
- **Responsibility**: Control flow graph (CFG) analysis and variable state transition modeling.
- **Key Classes**: `ControlFlowBuilder`, `CFGBlock`, `VariableTransition`.
- **Tests**: `tests/test_state_flow.py`.

---

## 6. Retrieval (`patchforge/retrieval/`)

### `hybrid.py` (CANONICAL)
- **Responsibility**: Multi-signal candidate retrieval combining BM25 lexical search, AST symbol proximity, test traceback correlations, and code graph distance.
- **Key Classes**: `HybridRepositoryRetriever`, `RetrievedCandidate`.
- **Tests**: `tests/test_hybrid_retriever.py`.

---

## 7. Integrations (`patchforge/integrations/`)

### `work_item.py` (CANONICAL)
- **Responsibility**: Normalized external issue representation. Implements strict path-traversal validation (`_validate_safe_path`) preventing directory traversal escapes (`..` or absolute paths).
- **Key Classes**: `PatchForgeWorkItem`.
- **Tests**: `tests/test_v19_phase5_external_integrations.py`.

### `github.py` (CANONICAL)
- **Responsibility**: GitHub Webhook and REST API ingestion handler. Verifies raw payload HMAC-SHA256 signatures (`X-Hub-Signature-256`) and tracks delivery IDs via in-process idempotency cache.
- **Key Classes**: `GitHubWebhookHandler`, `IngestionIdempotencyTracker`.
- **Tests**: `tests/test_v19_phase5_external_integrations.py`.

### `direct.py` (CANONICAL)
- **Responsibility**: Ingests direct JSON work item payloads with explicit schema validation.
- **Key Classes**: `DirectWorkItemIngestionHandler`.
- **Tests**: `tests/test_v19_phase5_external_integrations.py`.

### `swebench.py` (CANONICAL)
- **Responsibility**: Integration adapter for the official SWE-bench evaluation harness and dataset loader.
- **Key Classes**: `SWEBenchAdapter`, `EvalResult`.
- **Tests**: `tests/test_adapters.py`.

### `minisweagent.py` (ACTIVE SUPPORT)
- **Responsibility**: Bash/terminal tool execution adapter with built-in Windows WSL `E_ACCESSDENIED` and UTF-16 null-byte resilience.
- **Key Classes**: `MiniSWEAgentAdapter`, `ExecResult`.
- **Tests**: `tests/test_adapters.py`.

---

## 8. Models (`patchforge/models/`)

### `provider.py` (CANONICAL)
- **Responsibility**: Direct HTTP client to Ollama daemon (`/api/generate`) with configurable model name, temperature, and timeout.
- **Key Classes**: `OllamaProvider`, `OpenRouterProvider`.
- **Tests**: `tests/test_models.py`.

### `router.py` (ACTIVE SUPPORT)
- **Responsibility**: Model routing and fallback management across available local and cloud providers.
- **Key Classes**: `ModelRouter`.

---

## 9. Core (`patchforge/core/`)

### `config.py` (CANONICAL)
- **Responsibility**: Central configuration dataclass defining model names, timeouts, retry budgets, and workspace paths.
- **Key Classes**: `PatchForgeConfig`.

### `target.py` (CANONICAL)
- **Responsibility**: Core representations of candidate edit locations and line spans.
- **Key Classes**: `RepairTarget`, `EditSite`.

### `state.py` (ACTIVE SUPPORT)
- **Responsibility**: Dataclass representing intermediate repair state.
- **Key Classes**: `RepairState`.

### `orchestrator.py` & `orchestrator_v03.py` (LEGACY / OBSOLETE PROTOTYPES)
- **Responsibility**: Early V0.2/V0.3 prototypes. Retained for backwards-compatibility; superseded by `patchforge.pipeline.v1_orchestrator.V1RepairOrchestrator`.

---

## 10. Agent (`patchforge/agent/`)

### `controller.py` (ACTIVE SUPPORT / CANONICAL AGENT)
- **Responsibility**: Autonomous multi-turn ReAct tool-calling agent controller used in agentic evaluation modes.
- **Key Classes**: `AgentController`.
- **Tests**: `tests/test_controller_recovery.py`, `tests/test_agent_v03.py`.

### `state.py` & `policy.py` & `context.py` & `trajectory.py` (CANONICAL AGENT)
- **Responsibility**: Agent execution state machine, tool budget policies, context window compaction, and trajectory logging.
- **Key Classes**: `AgentState`, `AgentPhase`, `ExecutionEvidence`, `AgentPolicy`, `ContextCompactor`, `AgentTrajectory`.
- **Tests**: `tests/test_policy_guards_v03.py`, `tests/test_context_v03.py`.

---

## 11. Tools (`patchforge/tools/`)

### `base.py`, `editor.py`, `search.py`, `execution.py`, `reasoning.py`, `graph.py` (CANONICAL AGENT TOOLS)
- **Responsibility**: Sandboxed tool definitions exposed to `AgentController`:
  - `editor.py`: `ReadFileTool`, `ReadTestTool`, `ApplyPatchTool`.
  - `search.py`: `SearchCodeTool`, `SearchExactTool`, `FindSymbolTool`.
  - `execution.py`: `RunTargetedTestTool`, `RunReproductionTool`, `InspectFailureTool`.
  - `reasoning.py`: `FormulateHypothesisTool`, `ProposePlanTool`, `SpecifyRepairTool`.
  - `graph.py`: `InspectGraphTool`, `InspectGitHistoryTool`.
- **Tests**: `tests/test_tools_v03.py`.
