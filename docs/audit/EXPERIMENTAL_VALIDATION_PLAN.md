# Experimental Validation Plan

## Rules

Preserve `results/v19_orchestrator` unchanged. It is the historical control. Do not expose gold patches or hidden test patches to retrieval, prompts or policies. Pre-register each intervention, metrics, budget and pass/fail rule before its run.

## Measurement table

| Experiment | Control / treatment | Cohort | Success criterion |
|---|---|---|---|
| E1 workspace safety | V1 behavior / isolated worktree | synthetic + unit integration | zero caller-file changes, no cross-job collision. |
| E2 reconstruction | current reconstructor / CST candidate | six pre-harness V1.9 tasks | more harness submissions with no new syntax regressions. |
| E3 residual loop | one-shot/retry / preserved-candidate loop | four near misses | improve complete F2P; P2P must not decline. |
| E4 retrieval ablation | BM25 / +test / +graph / +temporal | 20-task analysis + pilot | predeclared localization gain and acceptable latency. |
| E5 model pilot | Qwen14B Q4 / candidate model | 5 representative tasks, then 20 if justified | better resolved-task efficiency, not headline F2P alone. |
| E6 serving | Ollama / alternative server | synthetic concurrent replay | higher stable throughput under equivalent model/context; no tenant cache leakage. |

## Metrics

Track fully resolved, F2P/P2P counts and regressions, patch validity, harness submissions, runtime by phase, attempts, model calls, input/output tokens, cache hit rate, cost, cost per resolved task, target localization recall, reconstruction failure category, environment failures and cancellation/recovery correctness.

## Decision gates

Run unit/integration tests first; then deterministic artifact replays; then 4–6 task pilot; then the frozen full cohort. A treatment that increases F2P but worsens P2P, submission integrity, or cost per resolved task is not promoted. The 20-task cohort is too small for strong statistical claims; report confidence intervals or uncertainty and reproduce promising results with fixed seeds/settings.
