# V2 Implementation Decisions

* Replaced destructive checkout cleanup with V2-owned workspace copies. A workspace can be deleted only when its V2 ownership marker matches.
* Reused `ResidualFailureRecord`, `EvalResult`, `AdaptiveVerificationVerdict`, and `FailureDrivenReplanner` as inputs to the next integration phase; they were not replaced because they already encode useful F2P/P2P evidence.
* Corrected the selected-test semantic boundary: a full SWE-bench harness run may not masquerade as a requested selector run.
* Reused the existing AgentController concept but did not promote it blindly: its live Docker/model path needs a service-backed pilot.
* Replaced hard-coded service assumptions with explicit runtime discovery/probes. PatchForge never starts Ollama or Docker automatically.
