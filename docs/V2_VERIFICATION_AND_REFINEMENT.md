# V2 Verification and Refinement

The official SWE-bench harness remains final acceptance. Existing `EvalResult`, adaptive verdict, residual record and replanner are retained.

`RunTargetedTestTool` no longer ignores `test_target` by substituting a full harness evaluation. It requires a selector-aware isolated executor and otherwise returns `SELECTED_TEST_UNAVAILABLE`. `LocalSelectedTestExecutor` is an explicit host-development option; production/untrusted repos require a container implementation. This boundary prevents false claims of targeted refinement.

`AssertionFailureExtractor` now converts bounded pytest-style assertion output into test identity, assertion source, expected value, actual value, and exception evidence. `ResidualFailureRecord` includes that evidence in its corrective guidance; `FailureDrivenReplanner` retains its existing `REFINE_SEMANTICS` action but now receives a narrower, evidence-backed micro-refinement instruction.
