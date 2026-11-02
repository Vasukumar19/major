# PatchForge V2 Architecture

V2 replaces shared mutable repair attempts with owned copy-on-write workspaces, an append-only candidate ledger, and explicit test observations. V1 remains available only as a historical/control path until a real repair pilot promotes V2.

`issue -> owned base workspace -> exploration/diagnosis -> candidate child workspace -> selected tests -> ledger accept/reject -> residual child -> official harness final gate`.

The implemented foundation is `patchforge.v2.workspaces`, `patchforge.v2.candidates`, `patchforge.v2.execution`, and `patchforge.v2.verification`. The existing AgentController/tool registry is a reuse candidate, not yet the V2 canonical runner.
