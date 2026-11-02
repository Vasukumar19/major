# PatchForge V1.9 Failure Analysis

## Raw-artifact accounting

`results/v19_orchestrator/summary.json` confirms 1/20 resolved, 14 valid patches and 14 harness submissions. The raw result is a historical baseline, not current code behavior. The pattern is more diagnostic than the headline: valid patch construction is common enough to reach the harness, yet complete behavioral resolution is rare.

| Failure family | Tasks / observed evidence | Most supported explanation |
|---|---|---|
| Pre-harness no-op / reconstruction rejection | flask-5063, pytest-5103, pytest-5227, pytest-5495, pytest-7220, pylint-5859 have `submitted_to_harness=false`; many first attempts say replacement is identical | Patch protocol/semantic validation rejects output before behavioral feedback. Confirmed artifact category; exact root cause per task requires full trajectories. |
| Structured syntax/semantic error | requests-2674 and pylint-7114 first attempts report unexpected character after line continuation; pytest-7490 has no-op then patch-syntax final state | Model output and reconstruction format mismatch. Confirmed observations. |
| Near-miss residual behavior | requests-1963 6/7 F2P, requests-2148 8/10, requests-2674 10/12 | Loop does not demonstrate assertion-level preservation/refinement. Probable causal explanation. |
| F2P success with regression | requests-863 4/4 F2P, 59/60 P2P | Acceptance is not a hard regression rollback barrier. Confirmed. |
| Wrong/broad diagnosis | Task artifacts include selected targets based on issue/retrieval before runtime failures | Plausibility is not execution evidence. Strong indication, not confirmed for every task. |

## Why sophistication did not translate

PatchForge computes a graph, temporal profile, behavior map, contract matrix, candidate profiles and competing hypotheses before testing the issue. Those components are not themselves failures, but the audit found no logged ablation showing that they improve target recall or final resolution. Meanwhile its critical deficits are simple operational ones: no isolated preserved candidate, late test feedback, and brittle edit application. This is likely a mismatch of investment rather than lack of concepts.

## Exact implementation weaknesses to address

* `v1_orchestrator.py:825` loops attempts but does not establish an accepted parent candidate with per-test invariants.
* `repair/reconstructor.py:30-115` does line/AST-span replacement and import healing; complex output failures exist in V1.9.
* `verification/tester.py` sends syntax-valid candidates to official evaluation; it offers no canonical targeted F2P execution API in the repair loop.
* `retrieval/hybrid.py` labels a token n-gram score “dense” and fuses heuristics without measured calibration.
* `v1_orchestrator.py:311-316` cleans shared state, blocking safe attempt comparison/concurrency.
