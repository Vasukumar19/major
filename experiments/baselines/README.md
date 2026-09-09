# Baselines — Phase B

Per spec §3: run ORIGINAL systems unmodified before building PatchForge.

## Status (2026-09-06) — ALL FOUR BASELINES PROVEN on psf__requests-863

| Check | Result |
|---|---|
| SWE-bench Lite dataset load | ✅ `SWE-bench/SWE-bench_Lite` test=300 via `datasets` (no key needed) |
| Docker daemon | ✅ RUNNING (started manually); eval image pulls + runs |
| `swebench` package | ✅ installed (`pip install -e third_party/SWE-bench`); ALWAYS run harness with `PYTHONUTF8=1` |
| Gold smoke (`patchforge-gold-smoke3`) | ✅ RESOLVED, F2P 4/4, P2P 63/63 — record `00_swebench_gold_smoke/psf__requests-863.json` |
| Agentless smoke (`patchforge-agentless-smoke`) | ✅ file→related→fine-grain→repair→eval loop runs; patch applied but UNRESOLVED (0/4 F2P, wrong-hypothesis miss on sessions.py) — record `01_agentless/psf__requests-863.json`, cost ~$0.052 / 9210 tokens |
| RepoGraph smoke | ✅ graph built (674 nodes / 4692 edges) + searcher queries resolve — record `02_repograph/psf__requests-863.json` |
| mini-SWE-agent smoke | ✅ model+env+loop, EXIT Submitted with correct answer, 6 msgs, repo untouched — script `03_minisweagent/mini_smoke.py`, record `mini_smoke_record.json` |
| `psf__requests-2679` | ✅ confirmed ABSENT from Lite. Never synthesize. |

## Lite pools relevant to cohort

- Flask (exactly 3 in Lite — cohort forced): `pallets__flask-4045`, `pallets__flask-4992`, `pallets__flask-5063`
- Requests (6 in Lite, pick 3): `psf__requests-1963`, `psf__requests-2148`, `psf__requests-2317`, `psf__requests-2674`, `psf__requests-3362`, `psf__requests-863`
- Django / Pytest / SymPy: many candidates — see `cohort.json` (PROPOSED, needs confirmation).

## Run order — DONE (single-instance proofs)

1. `00_swebench_gold_smoke` ✅ — gold eval green after 1 compat patch + `PYTHONUTF8=1`.
2. `01_agentless` ✅ — full localize→repair→eval loop on psf__requests-863 (UNRESOLVED is a valid result: proves the loop, classifies as WRONG_HYPOTHESIS).
3. `02_repograph` ✅ — graph build + queries, no LLM.
4. `03_minisweagent` ✅ — agent loop via WSL execution shim.

## Known provider/environment constraints (all documented, none upstream-modified)

- OpenRouter ignores `n>1` (returns 1 choice) → Agentless multi-sample batching needs sequential resampling in the PatchForge adapter; smoke used `num_samples=1`/`max_samples=1`.
- Agentless `--merge` assumes `num_samples>1` (crashes on single-sample dict) → smoke fed fine-grain output directly to `repair.py` (schema-compatible).
- Agentless diff assembly needs a from-scratch `playground/` checkout → smoke assembled the diff from `original/new_file_content` via `difflib` (adapter work).
- mini-SWE-agent `LocalEnvironment` uses CMD `shell=True` → smoke shimmed `_run` through WSL `bash.exe` (experiment script only).
- mini-SWE-agent cost tracker requires cost>0 → `cost_tracking=ignore_errors`; cap `max_tokens` (account credit limit hit at 117k-token default request).
- `mini` CLI needs a TTY (prompt_toolkit) → use the Python API (`DefaultAgent` + `LocalEnvironment` + `OpenRouterModel`), which is the integration surface anyway.

## Per-run record

Each run writes `<run_dir>/<instance>.json` with `{instance, model, runtime, tokens, cost, patch, tests, resolved}` + raw logs.
