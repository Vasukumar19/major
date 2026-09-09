# Phase C experiment — `psf__requests-863` integration test

Dev task, NOT the benchmark. Proves ONE complete PatchForge trajectory:
base commit → candidate patch → independent SWE-bench evaluation.

## Reproduce

```powershell
$env:OPENROUTER_API_KEY='<key>'
$env:OPENAI_API_KEY=$env:OPENROUTER_API_KEY
$env:OPENAI_BASE_URL='https://openrouter.ai/api/v1'
$env:PYTHONUTF8='1'
python experiments/phase_c/run_requests863.py
```

Docker must be running. First run clones `psf/requests` and builds the
RepoGraph `.pkl` (~2 min, cached afterwards); the SWE-bench image is cached
from Phase B.

## What was reused / added / changed

See `docs/PHASE_C_ARCHITECTURE.md` §§1–3. One line: upstream systems provide
localization, graph, execution, and grading; PatchForge provides evidence,
competing hypotheses, ranking, traceable patches, and the bounded loop.

## Result (2026-09-06)

- **RESOLVED**, attempt 1/3. F2P 4/4, P2P 60/60.
- Patch: 1 line in `requests/sessions.py` (H1-linked; alternative to the
  gold `models.py` fix, harness-verified).
- Model: `openai/gpt-4o-mini` throughout. Tokens 8,691/292. Cost **$0.0015**.
  Runtime 167s (spec budget 600s).
- Baseline A (Agentless): UNRESOLVED, ~$0.052. Baseline B: deferred to Phase D.

## Artifacts (`psf__requests-863/`)

| File | Content |
|---|---|
| `problem.json` | Allowlisted task data (no gold patch — enforced by `Problem`) |
| `evidence.json` | All candidate evidence |
| `hypotheses.json` | 3 ranked hypotheses with score breakdowns |
| `patch.diff` | Final unified diff |
| `tests.json` | Per-attempt test reports |
| `trajectory.json` | Stage timeline |
| `final_result.json` | Episode (spec §19 schema shape) |

## Remaining problems

See `docs/PHASE_C_ARCHITECTURE.md` §6. Headline: verbatim SEARCH matching is
the dominant failure mode; hypothesis file-diversity is prompt-level only;
Windows needs the documented WSL/`autocrlf`/`PYTHONUTF8` handling.
