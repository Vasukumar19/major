# Phase C Architecture — PatchForge orchestration layer

**Status:** implemented + proven end-to-end on `psf__requests-863` (RESOLVED, attempt 1).
**Date:** 2026-09-06. **Do NOT rerun discovery; do NOT run the 16-task benchmark.**

## 1. What was reused (nothing reinvented)

| Need | Source | PatchForge touchpoint |
|---|---|---|
| Benchmark + official eval | SWE-bench harness (subprocess) | `integrations/swebench.py` (+1 compat patch, `patches/`) |
| File/related/edit localization | Agentless CLIs (subprocess, `PROJECT_FILE_LOC` recipe) | `integrations/agentless.py` |
| Structural context | RepoGraph `CodeGraph` + `RepoSearcher` (build-once `.pkl`) | `integrations/repograph.py` (+4 patches, `patches/`) |
| Shell/test execution | mini-SWE-agent `LocalEnvironment` + `OpenRouterModel` | `integrations/minisweagent.py` (WSL shim on Windows) |
| Model access | OpenRouter (urllib, no new deps) | `models/provider.py`, `models/router.py` |

Upstream repos are unmodified except the five documented `patches/*.patch` files
(applied with `git apply`, never committed). Details: `docs/REUSE_AND_INTEGRATION_PLAN.md`.

## 2. What PatchForge added (the research layer)

```
issue → Agentless candidates → RepoGraph expansion → evidence aggregation
      → 3 competing hypotheses → deterministic ranking → hypothesis-tied
      minimal patch → git-apply gate → official eval → classify → bounded retry
```

- **Evidence** (`retrieval/evidence.py`): every decision cites typed evidence
  (11 types). No file/symbol/hypothesis without a WHY.
- **Localization** (`localization/localizer.py`): merges Agentless ranks with
  graph neighbors + issue/test mentions; deterministic weights; per-file
  diversity cap (`max_per_file=3`) so one file can't crowd out alternatives;
  issue-symbol bridge recovers symbols Agentless saw the file but not the
  symbol for (the exact `models.py`/`register_hook` gap from Phase B).
- **Hypotheses** (`reasoning/`): LLM proposes 3 hypotheses forced onto
  DIFFERENT primary files; deterministic ranker
  (semantic + structural + test + error + consistency; model confidence kept
  separate for auditability).
- **Patch** (`repair/`): SEARCH/REPLACE (tolerant parser) applied in-memory,
  emitted as unified diff; records `hypothesis_id → patch` traceability.
- **Loop** (`repair/loop.py`, `verification/`): ≤3 attempts total
  (untried hypotheses first, then one major retry with failure feedback);
  `git apply --check` gate before any Docker eval; failures classified as
  WRONG_LOCALIZATION / WRONG_HYPOTHESIS / PATCH_SYNTAX / PATCH_SEMANTICS /
  TEST_FAILURE / REGRESSION / TIMEOUT / INFRA_FAILURE — never "model failure".
- **Memory** (`memory/episode.py`): episode store only. No learning, no RL,
  no prompt optimization (explicitly deferred).
- **Budgets**: `max_hypotheses=3`, `max_patch_attempts=3`, test timeout 180s.

## 3. What changed vs Agentless / RepoGraph / mini-SWE-agent

- vs Agentless: it owns localization only. Reasoning (hypotheses, ranking),
  patch authorship, retry policy, and classification are PatchForge's.
  Provider fixes OpenRouter realities Agentless predates: sequential sampling
  (`n>1` ignored by provider), single-sample merge path, diff assembly
  without a from-scratch playground.
- vs RepoGraph: consumed as an evidence service (neighbors → Evidence),
  not a prompt add-on. Plus per-file diversity so graph-supported minorities
  survive the cut.
- vs mini-SWE-agent: execution primitives only (`run_bash` gate,
  `LocalEnvironment`); the agent loop never plans repairs.

## 4. Proven result (dev task, NOT benchmark)

`psf__requests-863` from clean base commit `a0df2cb`:

- H1 (Session `__init__` mishandles hook lists) → 1-line patch in
  `requests/sessions.py` → **RESOLVED: F2P 4/4, P2P 60/60**, attempt 1 of 3.
- Tokens 8,691 in / 292 out; **$0.0015**; 167s wall.
- Baseline A on the same instance: UNRESOLVED at ~$0.052 / 9,210 tokens.
- Note: gold fixes `models.py::register_hook`; PatchForge fixed the
  session-level handling instead. Both pass the independent harness —
  an alternative valid fix, not a leak (PatchForge never saw the gold patch;
  `Problem` cannot carry `patch`/`test_patch` — enforced + unit-tested).
- Baseline B (Agentless+RepoGraph): NOT run in Phase C; deferred to Phase D
  via `third_party/RepoGraph/run_repograph_agentless.sh`.

## 5. Exact run configuration

- Models: `openai/gpt-4o-mini` for Agentless steps (as `gpt-4o-mini-2024-07-18`),
  hypotheses, and repair. `meta-llama/llama-3.1-8b-instruct` API-validated,
  unused in this run.
- Key: `OPENROUTER_API_KEY`; Agentless via `OPENAI_BASE_URL` compat endpoint.
- Command: `python experiments/phase_c/run_requests863.py` (dev budget
  `max_task_seconds=1500`; spec target 600 — current 167s fits with margin).
- Artifacts: `experiments/phase_c/psf__requests-863/` +
  `experiments/phase_c/README.md`. Unit tests: `python -m pytest tests/`
  (39 passing, no network).

## 6. Known limitations (no silent workarounds)

1. SEARCH blocks must match verbatim; approximate context fails
   (`PATCH_SYNTAX`, loop moves on). No fuzzy matching in Phase 1.
2. Hypothesis quality bounds the loop: all-sessions hypotheses would miss a
   models-only fix; file-diversity prompting mitigates, not guarantees.
3. Single-sample Agentless path (`num_samples=1`) — provider ignores batched
   `n>1`; multi-sample voting deferred to Phase D baselines.
4. Windows needs WSL `bash.exe` for agent shell + `core.autocrlf=true`
   normalization in the apply gate; harness always runs with `PYTHONUTF8=1`.
5. OpenRouter credit caps bound `max_tokens` (2048) and parallelism (1 worker).
