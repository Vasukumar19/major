# Architecture Audit

## Observed execution path

`scripts/run_v05_cohort.py` selects `V1RepairOrchestrator`; `V1RepairOrchestrator.repair()` indexes a repository, creates issue/contract heuristics, retrieves candidates, asks the model for diagnosis, ranks sites, optionally probes, generates structured repair JSON, reconstructs source, writes it into the checkout, invokes the SWE-bench adapter, replans, saves an episode, and cleans up. The canonical method is 1,477 lines in `patchforge/pipeline/v1_orchestrator.py` and imports most subsystem types directly.

```
work item -> immutable job record -> isolated attempt workspace
         -> index/retrieve -> diagnose -> plan -> edit candidate
         -> static checks -> targeted F2P -> selected P2P -> candidate ledger
         -> accept / rollback / replan
```

## Findings

| Finding | Evidence | Severity | Confidence | Correction |
|---|---|---|---|---|
| God orchestrator and hidden control flow | `V1RepairOrchestrator.repair` begins at line 297 and contains all numbered stages/retry/persistence | High | Confirmed | Extract pure stage services; one reducer owns transition decisions. |
| Shared mutable checkout | `repair` invokes `git checkout .` and `git clean -fd` at lines 311-316, then writes source at 1125-1141 | Critical | Confirmed | Create an attempt worktree/container; never reset a caller-provided directory. |
| No restart-safe job state | result is accumulated in-process; episode save is terminal artifact, not a transactional state store | High | Confirmed | Persist job/attempt/state/event records before and after each side effect. |
| Retry policy has little observation | bounded `while attempt < max_repair_attempts` at 825; a model call follows, while test feedback is only available after harness | High | Confirmed | Drive retry from a typed failure observation and accepted prior candidate. |
| No cancellation/concurrency contract | no job identifier/lock/cancel token/worker abstraction in canonical repair path | High | Confirmed | Add cancellation checkpoints, lease-based worker ownership, workspace IDs. |
| “Dynamic” validation is normally absent | `enable_dynamic_probing=False` default, condition at 759 | Medium | Confirmed | Either measure and selectively enable it, or keep experimental outside canonical path. |

## Documentation-to-code status

| Claimed capability | Classification | Basis |
|---|---|---|
| canonical V1 orchestrator | Implemented and verified | Entry-point documentation and imports agree. |
| competing diagnosis / contract gate | Implemented but insufficiently tested empirically | Classes exist and unit tests exist; task artifacts commonly show generic unsupported hypotheses. |
| hybrid BM25/vector/RRF/reranker | Partially implemented / documentation overstates | `retrieval/hybrid.py` implements BM25 plus token n-gram “dense” score and weighted fusion; no embedding model, vector DB, RRF, or reranker is evident in that canonical file. |
| bounded AST editing / multi-site | Partially implemented | `reconstructor.py` line/AST-span editing and multi-edit overlap check exist, but V1.9 has six pre-harness failures and only single resolved task. |
| adaptive tiered verification | Partially implemented | harness provides F2P/P2P; no pre-harness targeted test runner is evidenced in canonical flow. |
| GitHub webhooks/direct/SWE-bench input | Implemented at adapter layer | work-item, GitHub, direct and SWE-bench modules exist; no durable API service/queue is present. |
| multi-user execution | Documented future / not implemented | no persistence, queue, tenancy or per-job isolation. |

## Recommendation

Do not introduce a hierarchical multi-agent framework. Make state transitions deterministic and auditable first. The model should produce diagnosis/plan/edit proposals; deterministic code should select allowed transitions, enforce budgets, retain candidates, and enforce isolation.
