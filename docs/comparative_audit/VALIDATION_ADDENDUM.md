# Comparative Audit Validation Addendum

This addendum validates the three comparative conclusions against the canonical V1 source (`patchforge/pipeline/v1_orchestrator.py`), the separate v0.3 agent path, verification code, runners, and V1.9 records. It corrects over-broad wording in the earlier comparative assessment.

## 1. Interactive exploration: present, but not in canonical V1.9

**Answer:** PatchForge has a real bounded interactive agent implementation, but canonical V1.9 does not invoke it.

| Evidence | Finding |
|---|---|
| `agent/controller.py:401-415` | `AgentController.run` constructs a stateful, turn-bounded tool session and can receive a `Tester`. |
| `tools/__init__.py:13-34` | Registry exposes search, read, symbol/reference lookup, graph/history inspection, patching, targeted test, reproduction and failure inspection tools. |
| `core/orchestrator_v03.py:56-85` | The v0.3 path instantiates that registry with a tester and runs `AgentController`. |
| `scripts/run_v05_cohort.py:371-385,524` | Historical/canonical V1.9 cohort creates `V1RepairOrchestrator`, not `AgentController`. |
| `v1_orchestrator.py:347-806` | V1 retrieves/indexes/diagnoses deterministically, optionally runs one gated dynamic probe, then enters structured repair; it offers the model no read/search/test action turn. |
| `scripts/run_task.py:69-82` | Its noncanonical `agent` mode registers tools *without* the available tester and tests only after `controller.run` returns. Therefore its targeted tool cannot use the container branch and defaults to host-execution denial. |

The earlier statement should read: **canonical V1.9 genuinely lacks interactive exploration, despite a separate existing v0.3 capability.** This is a connection/selection problem first, not proof that PatchForge must build an agent loop from zero.

Two caveats matter. The V1 dynamic probing path (`v1_orchestrator.py:759-786`) is runtime exploration of a preselected hypothesis, not model-directed repository exploration; it is disabled by default. The v0.3 controller is not automatically production-ready: its test tool and state/workspace lifecycle need validation before promotion.

## 2. Candidate loss: partial prompt preservation exists; tested state is lost

**Answer:** Candidate loss is real at the workspace/candidate level, but not total at the textual-feedback level.

| Evidence | What survives | What is discarded / overwritten |
|---|---|---|
| `v1_orchestrator.py:1304-1317` | `ResidualFailureRecord` is appended; for partial progress without regression, `last_working_replacement` keeps the model’s replacement text. | It does not retain modified file contents, a workspace snapshot, testable candidate identifier, or invariant list tied to a source digest. |
| `v1_orchestrator.py:844-880` | The next prompt receives `residual_guidance` and `prior_patch_code`. | Reconstruction still begins from `original_code`, which was read before attempts. |
| `v1_orchestrator.py:1404` | Failure details remain in `refinement_history`. | `_cleanup()` runs after every unresolved verification result. `_cleanup` is `git checkout .; git clean -fd` at `311-318`, resetting the just-tested candidate. |
| `v1_orchestrator.py:1251,1340,1406` | `applied_patch` refers to the latest candidate and becomes result patch on resolution/final return. | It is overwritten each attempt; there is no best non-regressed candidate selection. |
| `v1_orchestrator.py:1368-1402` | A hypothesis switch rebuilds target/context. | It explicitly clears `last_working_replacement`, discarding even the textual carry-forward. |
| `repair/loop.py:82-151` | Legacy `RepairLoop` has a `best_patch` and a near-miss retained-hypothesis policy. | It is a separate legacy loop, not called by canonical V1.9. It preserves a `Patch`, not a candidate filesystem state. |

The V1.9 data supports the importance of this gap: requests-2148 (8/10 F2P, 118/118 P2P) is exactly the sort of non-regressed partial candidate that should become the parent for a residual delta. V1 passes its replacement text back to the model, but cleans its source state before the next attempt. That makes preservation dependent on a new full replacement faithfully reproducing the prior patch.

**Correction:** do not describe V1 as having no partial-patch preservation. Describe it as having **prompt-level partial preservation but no persistent, testable candidate lineage or best-candidate selection**. Connecting `last_working_replacement` to an owned candidate worktree is a focused redesign; replacing the entire repair schema is not required.

## 3. Targeted residual verification: contracts exist; execution is not wired correctly

**Answer:** the data and feedback infrastructure is substantially reusable, but the actual targeted execution path needs an adapter addition and canonical-loop integration.

### Already present and reusable

* `integrations/swebench.py:24-47,139-163`: `EvalResult` records F2P/P2P success/failure identities, counts and captured output.
* `verification/adaptive.py:87-124`: `AdaptiveVerifier` constructs a verdict with F2P/P2P counts and condensed error details.
* `repair/residual.py:48-133`: `ResidualFailureRecord.from_verdict` extracts passed/failed F2P lists and corrective guidance.
* `repair/replanner.py:104-141`: partial F2P success produces explicit “preserve working logic” guidance.

### Current blockers

* `AdaptiveVerifier.verify` calls `tester.run(instance_id, patch_text)` once. `Tester.run` calls `SWEBenchAdapter.evaluate`, which invokes the official harness; it does not accept a selected test list.
* `RunTargetedTestTool.execute` accepts `test_target`, but when a `Tester` is available it ignores that argument and calls the same `tester.run(...)` full harness (`tools/execution.py:31-59`). Its only selector-respecting branch is host `pytest`, which is intentionally disabled by default and is not containerized (`:62-105`).
* Canonical V1 never creates or calls this tool. Thus neither the existing selector argument nor `ResidualFailureRecord` yields a targeted residual loop in V1.9.
* The official harness output can identify F2P test IDs after a full run, but PatchForge needs a controlled task-environment runner to execute those IDs against a candidate workspace. That API is absent.

## Component decision

No wholesale verification rewrite is required. Retain `EvalResult`, `AdaptiveVerificationVerdict`, `ResidualFailureRecord`, `FailureDrivenReplanner`, static validation and the final official-harness gate. Add an isolated `run_selected_tests(candidate_workspace, selectors, timeout)` adapter with typed output; make `RunTargetedTestTool` use it; and invoke it from the V1 successor after an initial full-harness/manifest discovery. This change depends on candidate workspaces, because selected tests must run against the preserved candidate rather than the base checkout.

## Prioritized conclusion

1. **Connect, then harden:** reuse the v0.3 tool/controller concepts only after passing a tester through the active runner and enforcing V1-grade isolation.
2. **Fix candidate state before adding more intelligence:** the essential missing artifact is a durable candidate workspace/digest, not another hypothesis type.
3. **Extend—not replace—verification:** existing result and residual models are sufficient; selected-test execution and canonical wiring are missing.
