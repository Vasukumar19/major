# Phase D protocol (frozen 2026-09-06; authorized, no system changes)

Cohort: `experiments/baselines/cohort.json` (16 IDs, APPROVED — django 4,
flask 3, pytest 3, requests 3, sympy 3). `psf__requests-2679` does not exist.

## Arms (one instance per `run_arm.py` invocation; resume-safe)

- **A** = Agentless localize (file→related→fine, `num_samples=1`,
  `gpt-4o-mini-2024-07-18` via OpenRouter compat) + repair (`max_samples=1`
  greedy, `--gen_and_process`) + official eval. Reduced-sample baseline:
  OpenRouter ignores batched `n>1`, so upstream 40-sample sweeps are
  impossible on this provider; A/B/C all use single-sample generation.
- **B** = RepoGraph fork (`third_party/RepoGraph/agentless/`) localize
  (3 stages + `--repo_graph`, `--num_samples 1`, hardcoded `gpt-4o-2024-05-13`)
  + fork repair (`--max_samples 1`, `--repo_graph`) + official eval.
  Graph/structure cache staged to `third_party/RepoGraph/repo_structures/`
  per run and removed afterwards (keeps the clone clean); tags converted
  from our jsonl build to the fork's `tags_{id}.json` array format.
- **C** = PatchForge `Orchestrator.run_instance` unchanged (its artifacts land
  under `experiments/phase_c/`; the per-task record is copied here).

## Identical conditions

- Same 16 tasks, same base commits (fresh `ensure_checkout`, clean-tree check).
- Same harness: `--timeout 180` for every eval (validated: gold resolves at
  180s on django__django-10914 and sympy__sympy-12419).
- Same classification (`patchforge.verification.classifier`, frozen).
- Cost convention: Agentless-side tokens × provider list prices
  (mini $0.15/$0.60, gpt-4o $5/$15 per 1M in/out, flagged `cost_estimated`);
  PatchForge-side exact OpenRouter `usage.cost`.
- Free-tier OpenRouter key: 402/429 responses are recorded as INFRA_FAILURE
  with the error text, never retried silently.

## Records

`results/phase_d/<ARM>/<instance>.json` — instance, base_commit, status,
failure_stage, localization_top1/topk, hypotheses_generated,
selected_hypothesis, patch_attempts, patch_valid, FAIL_TO_PASS,
PASS_TO_PASS, input/output/total tokens, cost, runtime. Raw work + harness
logs sit alongside (`results/phase_d/work/`, `logs/evaluation/phased-*`).

## Forbidden in Phase D

Fuzzy matching, multi-sample voting, temporal graphs, prompt optimization,
any `patchforge/` or `third_party/` modification. Evidence first.
