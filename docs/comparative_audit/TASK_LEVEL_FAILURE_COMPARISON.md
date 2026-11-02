# Task-Level Failure Comparison

## Method limitation

The audit read stored V1.9 task records and patches. It did not check out each historical upstream base repository or run a reference agent. Thus the “reference-system response” column describes a **source-observed mechanism that would create a different opportunity**, not a claim that it would solve the task.

| Task | PatchForge stored target / outcome | Precise stored failure evidence | Reference mechanism relevant | Confidence |
|---|---|---|---|---|
| requests-863 | `requests/models.py:Request`; 4/4 F2P, 59/60 P2P | Final class `REGRESSION` | Agentless-style multi-candidate validation or a V2 candidate ledger could select/reject based on P2P. | Confirmed regression; hypothetical alternative outcome. |
| requests-2148 | `requests/sessions.py:Session`; 8/10 F2P, 118/118 P2P | Unresolved with no P2P regression | SWE-agent tool loop can inspect/run residual tests; PatchForge needs preserved residual delta loop. | Confirmed near miss; probable loop gap. |
| requests-2674 | `requests/models.py:content`; 10/12 F2P, 139/142 P2P | First semantic validation has line-continuation syntax error; final semantics failure | Aider no-match/error observations and multi-candidate selection target this format/repair failure. | Confirmed failure; hypothetical outcome. |
| requests-1963 | `requests/models.py:prepare`; 6/7 F2P, 110/112 P2P | Partial repair plus two regressions | Candidate ledger needs both residual targeting and rollback. | Confirmed. |
| flask-5063 | `blueprints.py:register`; no harness | First attempt no-op validation | Aider-style no-op corrective observation; Agentless can sample alternatives. | Confirmed pre-harness rejection. |
| pytest-5103 | `fixtures.py:pytestconfig`; no harness | No-op validation | Same. | Confirmed. |
| pytest-5227 | `python.py:collect`; no harness | No-op validation | Same. | Confirmed. |
| pytest-5495 | `pytester.py:assert_contains`; no harness | No-op validation | Same. | Confirmed. |
| pytest-7220 | `capture.py:readouterr`; no harness | No-op validation | Same. | Confirmed. |
| pylint-5859 | `pylinter.py:set_option`; no harness | No-op validation | Same. | Confirmed. |
| flask-4045 / pytest-5221 / pytest-7373 / pylint-7228 | Harness submitted, F2P incomplete | Stored failures show broad unrelated/full-suite failures but no complete repair | Reproducer/targeted test execution can disambiguate environment noise versus issue residual. | Strong indication. |

## Required per-task replay protocol

Before asserting target error or comparing outcomes, reproduce in an isolated historical base checkout; retain the issue text, PatchForge retrieved candidates, prompt, response, parsed edit, reconstructed diff, individual F2P/P2P outcomes and environment log. Then run the chosen reference implementation with identical model, time/cost and allowed command budget. This is the only valid way to make task-level efficacy claims.
