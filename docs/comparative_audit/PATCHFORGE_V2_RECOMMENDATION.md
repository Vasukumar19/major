# PatchForge V2 Comparative Recommendation

## End-to-end sequence

1. Normalize issue and create an immutable job record.
2. Create a unique base-pinned worktree/container and source manifest.
3. Build/cached deterministic index; run cheap issue-to-file retrieval.
4. Let a bounded tool loop inspect/search/read and, when available, run a reproducer/targeted test. Each output becomes a typed observation.
5. Produce a hypothesis and repair plan only after evidence identifies allowed files/spans and explicit invariants.
6. Generate one or a small bounded candidate set using a structured action or search/replace protocol. Apply in isolated candidate workspaces.
7. Static validate, run target F2P, record individual outcomes; accept an improving non-regressed parent candidate.
8. For residual failures, provide one failing test/assertion plus minimal code/evidence and request a narrow delta.
9. Run affected tests and P2P selection; rollback rejected candidates. Run official harness as final acceptance.
10. Persist event history, diffs, test reports, costs and final explanation; support cancel/resume.

## Design rationale

The exploration loop is motivated by SWE-agent’s repeated action/observation architecture. Candidate diversity and hierarchical localization are motivated by Agentless. Source-grounded edit mismatch feedback is motivated by Aider. Durable event/state boundaries are motivated by OpenHands. PatchForge’s own raw results motivate prioritizing reconstruction and residual verification. None establishes a guaranteed benchmark gain.

## Component decisions

| Component | Decision | Motivation |
|---|---|---|
| V1 orchestrator | Replace in critical path | monolith/mutable cleanup obstruct recovery and candidate selection. |
| Graph/test index | Keep, refactor | deterministic tool support; measure usefulness. |
| Temporal graph/counterfactual scoring | Experimental gate | no causal V1.9 value evidence. |
| JSON schema | Keep | auditable bounded output. |
| Mutation engine | Add alternative SEARCH/REPLACE; characterize CST | pre-harness failures need recoverable exact-match diagnostics. |
| Verification | Redesign | target/residual/P2P candidate ledger is missing. |
| Model layer | Keep abstraction, instrument | avoid confounded model swap. |
| Workspace/persistence | Introduce | safety, concurrency, resume and audit. |
