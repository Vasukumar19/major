# Repair and Verification Audit

## Observed design

The repair path uses `RepairUnitPlanner`, structured JSON actions, `SourceReconstructor`, static validation, `AdaptiveVerifier`, official SWE-bench evaluation, and `FailureDrivenReplanner`. `SourceReconstructor` performs AST-span or line-range replacement and attempts automatic missing-import healing (`repair/reconstructor.py:30-115, 337-475`). Multi-edit overlap detection exists (`115-210`).

## Evidence

V1.9’s 14 valid patches but only 1 resolved task directly demonstrates a large post-generation quality gap. Six tasks are not submitted to the harness, so reconstruction/semantic validation is a concrete gating failure. Near misses demonstrate partial behavior repair but no mechanism that preserves a candidate while making the smallest residual change. `requests-863` proves F2P-only progress is unsafe: 4/4 F2P with a P2P regression.

## Findings

| Finding | Severity | Confidence | Correction |
|---|---|---|---|
| Line/AST span reconstruction can alter formatting/indentation and auto-import code guesses dependencies | High | Confirmed by implementation; V1.9 pre-harness failures correlate but do not prove every cause | Replace only bounded Python edit operations with CST-preserving transforms after characterization tests. |
| The harness is the first observed targeted test feedback in canonical flow | High | Confirmed | Run known F2P tests, when safely available from harness metadata, before broad P2P; persist parsed failures. |
| Candidate patch preservation is absent | High | Strong indication | Store accepted workspace snapshot and invariant list; apply refinements to it rather than re-generating from base. |
| P2P regressions do not imply automatic rollback ledger | High | Confirmed from 34 regressions in summary | Reject regressed candidate, restore last non-regressed candidate, feed minimal regression observation. |
| Failure output is not normalized into a stable actionable contract | Medium | Strong indication | Define `FailureObservation`: test ID, assertion, exception, trace frames, phase, environmental classification. |

## V2 feedback loop

1. Start with base workspace and explicit test manifest.
2. Generate one bounded candidate; static/CST/patch scope validate it.
3. Run targeted F2P; record every individual test outcome.
4. Preserve a candidate only when it improves F2P and introduces no selected regression.
5. Construct a residual prompt from one failing assertion plus the minimal dependency context and passing invariants.
6. Apply one bounded delta to the preserved candidate.
7. Run affected F2P plus selected P2P; on regression rollback deterministically.
8. End on all F2P plus selected/full P2P, or explicit budget exhaustion with artifact trail.

Validate this design first on the four near misses. It is a hypothesis, not a claimed performance gain.
