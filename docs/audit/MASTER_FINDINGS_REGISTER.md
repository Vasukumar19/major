# Master Findings Register

| Priority | ID | Finding | Category | Severity | Confidence | Evidence | Action / dependency | Validation |
|---:|---|---|---|---|---|---|---|---|
| 1 | CQ-01 | Shared checkout is forcibly reset/cleaned | Correctness/security | Critical | Confirmed | `v1_orchestrator.py:311-316` | Per-attempt workspace; Stage 1 | dirty-repo non-mutation test |
| 2 | CQ-06 | SWE-bench checkout destination can collide | Scalability | High | Confirmed | `swebench.py:77-89` | immutable base cache/worktrees; Stage 1 | concurrent same-repo integration |
| 3 | AR-01 | Canonical orchestrator combines all responsibilities | Architecture | High | Confirmed | `v1_orchestrator.py` 1,477 lines | explicit state machine; Stage 2/6 | transition/failure-recovery tests |
| 4 | RV-01 | Valid-patch to resolved gap is large | Repair/verification | High | Confirmed | raw V1.9: 14 valid, 1 resolved | candidate ledger/residual loop; Stage 2/3 | near-miss pilot |
| 5 | RV-02 | Six tasks fail before harness | Repair | High | Confirmed | V1.9 `submitted_to_harness=false` | characterize/rewrite reconstruction; Stage 2 | more submissions, no regression |
| 6 | RV-03 | Regression protection is insufficient | Verification | High | Confirmed | 34 P2P failures; requests-863 4/4 F2P, 59/60 P2P | rollback/invariant guard; Stage 3 | rejection/rollback tests |
| 7 | RI-01 | Hybrid retrieval nomenclature overstates implementation | Retrieval/docs | Medium | Confirmed | `hybrid.py` BM25 + n-gram weighted score; no evident embeddings/RRF/reranker | correct docs; ablate signals; Stage 4 | instrumentation report |
| 8 | RI-02 | Repository intelligence has no demonstrated causal value | Retrieval | High | Confirmed | no retrieval recall or ablation artifact found; dynamic probes off | measurement before expansion; Stage 4 | pre-registered ablation |
| 9 | LM-01 | Model selection/configuration is unmeasured | Model | High | Confirmed | no current comparable model experiment; no context/token telemetry | instrument then pilot; Stage 5 | repeatable comparison |
| 10 | LM-02 | Inference serving migration lacks trigger evidence | Performance | Medium | Confirmed | single-job historical baseline; no utilization/concurrency measures | load test before vLLM/SGLang; Stage 5 | concurrent replay |
| 11 | CQ-03 | Mutation boundary lacks evident containment enforcement | Security | High | Confirmed | `v1_orchestrator.py:1125-1141` | resolve/contain every write; Stage 1 | traversal/symlink tests |
| 12 | CQ-04 | Broad exception handling hides operational cause | Reliability | Medium | Confirmed | e.g., cleanup lines 313-316 | typed errors/events; Stage 1 | fault injection |
| 13 | CQ-05 | Docker PATH is Windows-specific | Portability | Medium | Confirmed | `swebench.py:110-112` | runner config adapter; Stage 1 | OS matrix |
| 14 | PR-01 | No durable jobs, cancellation, tenancy or recovery | Production | High | Confirmed | canonical code has no queue/database/job state | persistent job service; Stage 6 | restart/cancel/tenant tests |
| 15 | TE-01 | Current full test result cannot be reproduced here | Testing | Medium | Confirmed environment condition | pytest temp-dir creation error in `test_adapters` setup | controlled CI temp root; Stage 1 | full suite rerun |
| 16 | RS-01 | Benchmark baseline differs from audited revision | Evaluation | Medium | Confirmed | V1.9 `da203c8` + dirty hash vs current `a639dba` | label baseline historical; no direct causation claims | revision manifest |

Findings not listed are not implicitly verified. In particular, no audit finding asserts that a particular model, graph algorithm, or framework will improve resolution without experiment.
