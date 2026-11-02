# Source-Code Comparison

## Stage-by-stage PatchForge baseline

| Stage | PatchForge input/output and implementation | Model involvement / failure path | Tests / evidence |
|---|---|---|---|
| Intake | `Problem` from `SWEBenchAdapter.load_problem`; checkout in `ensure_checkout` | None; checkout force-cleans fixed destination | adapter/integrity tests; unsafe for concurrent reuse. |
| Index | `RepositoryIndexer.index()` parses Python in thread pool; graph/test index cached | None | `test_repository_intelligence.py`; no localization recall artifact. |
| Retrieve | `HybridRepositoryRetriever.retrieve()` | None; BM25 plus weighted token n-gram/graph/test/temporal signals | `test_hybrid_retriever.py`; not an embedding/RRF/reranker implementation. |
| Diagnose | `IssueBehaviorExtractor`, contract classes, then provider call at `v1_orchestrator.py:606` | LLM returns competing diagnosis; generic/incorrect target can propagate | diagnosis/contract tests; V1.9 diagnoses are stored. |
| Plan/edit | `RepairUnitPlanner`, `StructuredRepairPromptBuilder`, parser, `SourceReconstructor` | LLM produces JSON action, then deterministic source mutation | structured/reconstruction tests; no-op/syntax failures occur in artifacts. |
| Validate | static AST then `AdaptiveVerifier`/SWE harness | model only sees outcome on retries | V1.9 14 submissions, 6 pre-harness rejects. |
| Replan | `FailureDrivenReplanner` in loop at `:825` | chooses limited action / next generation | bounded to 3 attempts; no durable candidate ledger. |

## Exact comparisons

| Dimension | PatchForge | Reference source mechanism | Exact difference / consequence | Action |
|---|---|---|---|---|
| Control loop | One large `repair()` method, fixed numbered stages | SWE-agent `DefaultAgent.step`, `RetryAgent.run`; OpenHands `Agent.astep` | Reference model receives tool observations between decisions. PatchForge gets no interactive exploration after an initial target. | Add a small bounded explore/test loop before final planning. |
| Model output errors | JSON parser/reconstructor fails into replanner | SWE-agent parser emits format errors; Aider reports failed SEARCH blocks and similar lines | Both ref systems turn protocol failure into next model context. PatchForge records a failure but does not give a location-aware match diagnostic. | Emit `EditApplicationObservation` with source snippet/ambiguity/no-op. |
| Localization | Heuristic retrieval and diagnosis select site early | Agentless does hierarchical LLM file/symbol/line localization; AutoCodeRover research uses structural search/SBFL | PatchForge has more static machinery but less execution-grounded target recovery. | Measure staged localization and add trace/test evidence before target lock. |
| Candidate management | One live workspace state, overwritten and cleaned | Agentless produces multiple generations; SWE-agent retry/reviewer retains trajectories; Aider uses git-oriented interaction | PatchForge cannot choose a safer partial candidate or resume from it. | Candidate DAG, isolated worktree and selection policy. |
| Context | Large precomposed diagnosis/repair prompts | SWE-agent truncates observations with instruction; OpenHands state is event-derived/condensed | PatchForge lacks measured prompt composition/truncation and stable observation history. | Token ledger + typed compressed observations. |
| Verification | Official harness is central test path | Agentless patch validation/reranking; SWE-agent agents can run commands during loop | PatchForge feedback comes late and does not preserve accepted portions. | Targeted-test API and candidate-level result ledger. |

## Causality classification

The differences above are **confirmed code differences**. Their expected benefit is **probable**, except PatchForge’s six pre-harness failures, where better edit-error feedback directly targets an observed failure category. Claims that a tool loop or alternative editor solves specific tasks remain **hypotheses** until controlled replay.
