# PatchForge AI — Executive Technical Assessment

**Audit date:** 2026-11-02. **Audited revision:** `a639dba27c2343b28bbd38313d1836f4c605c3e5` on `main` (clean tree). **Historical V1.9 evidence:** `results/v19_orchestrator/summary.json`, generated on `da203c8` with a non-empty dirty-diff hash; it is a valid historical baseline, not proof that the present revision produces identical results.

## Decision

PatchForge V1.9 is a useful research prototype with sound safety intentions, but it is not yet a sound production repair architecture. Retain the normalized work-item boundary, provider abstraction, harness adapter, structured patch schema, static validation, and benchmark artifacts. Replace the monolithic mutation-in-place execution model with an explicit persisted job state machine and per-attempt workspace. Simplify unmeasured intelligence layers until ablation data shows they improve localization or repair.

The current bottleneck is not demonstrated to be the choice of Qwen alone. V1.9 produces valid patches for 14/20 tasks yet resolves 1/20; 6 tasks never reach the harness and the near misses retain failing F2P tests. This is stronger evidence for reconstruction/retry/verification weaknesses than for a wholesale model replacement.

## Verified baseline

| Measure | Raw V1.9 value |
|---|---:|
| Cohort | frozen 20-task SWE-bench Lite cohort |
| Fully resolved | 1/20 (5%) |
| F2P | 32/54 (59.3%) |
| P2P | 1081/1115 (97.0%); 34 regressions |
| Valid patches | 14/20 |
| Harness submissions | 14/20 |
| Total runtime | 8598.51 s |

The raw summary confirms the aggregate counts. `psf__requests-863` achieves 4/4 F2P but regresses 1 P2P; `psf__requests-2148` is 8/10 F2P and 118/118 P2P; `psf__requests-2674` is 10/12 F2P and 139/142 P2P; `psf__requests-1963` is 6/7 F2P and 110/112 P2P. Six entries have `submitted_to_harness=false`.

## Final decisions

1. **Fundamentally sound?** The stage order is defensible; its coupling, mutability, persistence, and evidence gates are not production-ready.
2. **Three largest architecture mistakes:** one 1,477-line orchestrator owns policy, I/O, state, retries, and persistence; it mutates/cleans a shared checkout; and it treats many heuristic intelligence signals as inputs without measuring their causal contribution.
3. **Five code-level mistakes:** destructive `_cleanup()` (`v1_orchestrator.py:311-316`); writes based on model-provided relative paths (`:1125-1141`) without an evident containment check at that boundary; silent broad `except`; platform-specific Docker PATH injection (`swebench.py:110-112`); test suite depends on ambient OS temp-directory state (current run fails before test logic).
4. **Repository intelligence helping?** Unknown overall. It supplies deterministic context, but no logged retrieval recall/precision or component ablation links it to resolved repairs. Dynamic probes are disabled by default.
5. **Right LLM?** Qwen2.5-Coder 14B Q4 is an acceptable local baseline, not a justified final choice. Its configuration and effective context are unmeasured.
6. **One or multiple models?** Start with one configurable repair model plus deterministic helpers. Introduce a second model only after a measured routing experiment proves a gain per resolved task.
7. **Local or cloud?** Keep local for privacy/offline baseline; add an opt-in provider route for difficult jobs after redaction, budget, and evaluation controls exist.
8. **Patch strategy?** Structured actions and static checks are worth retaining, but line/AST reconstruction and auto-import healing are too fragile as the primary mutator.
9. **Refinement sufficient?** No. It retries up to three times but lacks persistent candidate acceptance, targeted residual-test execution, and regression rollback.
10. **Primary block to more solves:** weak evidence from actual failing tests, brittle reconstruction, and non-preserving refinement—before attributing failure to model capability.
11. **Retain:** work-item guards, provider interface, official harness integration, raw artifacts, candidate identity work, structured schema.
12. **Remove/sunset:** legacy orchestrators from new development; unvalidated intelligence paths from critical routing.
13. **Redesign:** workspace/job lifecycle, verification feedback loop, and orchestration boundaries.
14. **V2:** deterministic state machine around isolated attempts, measured retrieval, bounded CST edits, and candidate ledger.
15. **First implementation:** Stage 1 safety/correctness fixes and tests—not model migration.

See [MASTER_FINDINGS_REGISTER.md](MASTER_FINDINGS_REGISTER.md), [PATCHFORGE_V2_ARCHITECTURE.md](PATCHFORGE_V2_ARCHITECTURE.md), and [PATCHFORGE_V2_ROADMAP.md](PATCHFORGE_V2_ROADMAP.md).
