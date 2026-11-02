# PatchForge Deep Comparative Assessment

**Scope.** PatchForge source is audited at `a639dba`; V1.9 artifacts are historical (`da203c8` plus a recorded dirty diff). Reference source locations below are default-branch snapshots read on 2026-11-02; URLs deliberately identify source rather than claiming a stable release tag. No reference implementation was executed against PatchForge tasks.

## Answer

Established systems do not win merely because they have more sophisticated architecture. Their strongest reusable mechanisms close the observation–action loop: the model can inspect, command, see exact results, correct malformed actions, and retain/select candidates. PatchForge front-loads static/heuristic diagnosis, commits to a target early, then gives its model at most three patch-generation attempts; its first task-specific execution observation commonly arrives only after a complete patch is reconstructed and sent to the harness.

This is a **confirmed implementation difference**, not proof that any reference system would solve a particular PatchForge task.

## Five most important differences

| Rank | Difference | Evidence | Importance |
|---:|---|---|---|
| 1 | Interactive tool observations vs fixed pre-edit pipeline | SWE-agent run/step loop; PatchForge `V1RepairOrchestrator.repair` | Wrong targets and assumptions can be corrected before submission. |
| 2 | Candidate sets and selection vs a single mutable candidate | Agentless samples/validates candidates; PatchForge overwrites checkout during attempts | V1.9 has 14 valid patches but 1 resolved; partial candidates/regression-free alternatives are lost. |
| 3 | Execution-derived localization vs issue/AST heuristics alone | AutoCodeRover supports SBFL; Agentless staged localization; PatchForge calls retrieval before failure traceback | PatchForge’s target can be plausible but unsupported by failing behavior. |
| 4 | Edit-application errors made actionable vs reconstruction rejection | Aider `EditBlockCoder.apply_edits`; PatchForge reconstructor/replanner | Six V1.9 tasks never reach the harness. |
| 5 | Persistent trajectories/events vs terminal result artifact | SWE-agent trajectory; OpenHands event conversation; PatchForge result/episode files | Enables replay, recovery, candidate comparison and observability. |

## What to borrow first

1. A restricted exploration tool loop (read/search/test/diff), bounded by commands, time and token budget.
2. A candidate ledger with isolated workspaces and deterministic acceptance/rollback.
3. Typed test observations driving localization and residual repair before the official final harness.
4. Search/replace as an alternative edit protocol—not wholesale Aider reuse—and an edit-application diagnostic path.
5. Measured hierarchical localization/coverage experiments before expanding graphs.

## Preserve or simplify?

Preserve structured work-item validation, provider abstraction, source/test index, patch schema, official harness adapter and raw artifacts. Fundamentally simplify critical-path orchestration: make it a persisted state machine around observable exploration, candidate selection and test feedback. Gate temporal graphs, counterfactual scoring and pseudo-dense retrieval until ablation proves value.

The most likely route to higher complete resolution without increased regressions is not a larger model alone: preserve a non-regressed partial patch, expose the smallest remaining failure to the model, make one bounded delta, and reject/rollback any regression. Validate this on the four V1.9 near misses before a full run.
