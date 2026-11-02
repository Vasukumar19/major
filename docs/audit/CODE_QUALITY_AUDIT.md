# Code Quality Audit

## Confirmed findings

| ID | File and symbol | Severity | Evidence / reproduction | Consequence | Correction and tests |
|---|---|---|---|---|---|
| CQ-01 | `patchforge/pipeline/v1_orchestrator.py:_cleanup` (311-316) | Critical | Calling `repair()` runs `git checkout .` and `git clean -fd` in the supplied `repo_dir`; errors are swallowed. | Deletes caller changes/untracked files and races another job. | Require a PatchForge-created worktree marker; delete only that workspace. Test a dirty user repo is rejected untouched. |
| CQ-02 | `v1_orchestrator.py:repair` | High | 1,477 lines; it owns indexing, prompting, edits, writes, tests, replan, cleanup and episode persistence. | Changes are hard to reason about and partial failures cannot be recovered. | Stage interfaces + reducer; transition and failure-injection tests. |
| CQ-03 | `v1_orchestrator.py:1125-1141` | High | reconstruction paths are joined to `repo_p`, directories are created and files are written. No containment verification is evident at this mutation boundary. | A malformed/malicious repair action could escape intended repository semantics depending on upstream path normalization. | Resolve path and require `is_relative_to(repo_root)` before every write; test `../`, absolute, drive, symlink paths. |
| CQ-04 | `v1_orchestrator.py:313-316` and broad catches throughout | Medium | failures in cleanup are discarded; many subsystem failures collapse to generic results. | Diagnosis and operational failure are conflated. | Catch narrow exceptions, preserve cause in typed error/event; fault-injection tests. |
| CQ-05 | `integrations/swebench.py:110-112` | Medium | hard-coded Windows Docker installation directory is injected into PATH. | Non-portable production behavior; environment dependence. | Make runner resolution config/platform adapter; Windows/Linux tests. |
| CQ-06 | `integrations/swebench.py:77-89` | High | checkout uses fixed `repos_dir/repo` destination, then forced checkout/clean. | Concurrent jobs for same repo collide and user data may be reset if root is misconfigured. | unique immutable base cache + worktree per attempt, lock base cache; concurrency test. |
| CQ-07 | `verification/tester.py:72-86` | Low | temporary prediction workspace is properly removed, but run artifact lifetime depends on external harness. | Diagnosing later requires external log stability. | Copy/summarize result logs into job artifact store; recovery test. |
| CQ-08 | test environment | Medium | The initial `python -m pytest tests -x -vv` stopped because pytest could not create its numbered temp directory. A controlled `--basetemp` rerun passed 43 tests, then `tests/test_dynamic_experiment.py::test_probe_lifecycle_and_rollback` failed because that test directly uses the managed system `tempfile` directory and cannot write there. | Current full-suite pass claim cannot be independently reproduced in this environment; this is environment evidence, not a product bug. | Configure `TMP`/`TEMP` to a controlled writable CI directory or inject a temp root in tests, then record the full result. |

## Test assessment

332 tests are collected. They are predominantly offline unit/mocked integration tests, which is appropriate for speed but does not demonstrate a live model, Docker lifecycle, clean-workspace recovery, or concurrent-job behavior. The audit did not modify source or rerun the frozen benchmark.

## Dead/duplicate-risk inventory

The repository has several legacy and experimental paths (`core/orchestrator.py`, `core/orchestrator_v03.py`, `pipeline/baseline.py`, `pipeline/graph_engine.py`, agent/reasoning/experiment packages). Their existence is not proof of dead code. New V2 work should depend only on the declared canonical interfaces; use import/runtime tracing before deletion.
