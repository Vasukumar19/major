# Verification and Refinement Review — PatchForge V2
**Document ID:** `docs/code_critical_review/VERIFICATION_AND_REFINEMENT_REVIEW.md`  
**Review Type:** Verification Infrastructure & Residual Feedback Forensics  
**Author:** Principal Autonomous Systems Engineer & Code Critic  
**Date:** 2026-11-03  

---

## 1. Executive Summary

Verification is the primary reality check of autonomous repair. Without rigorous, containerized execution, an agent operates in an ungrounded hallucination loop.

This audit critically examines PatchForge's test execution infrastructure (`patchforge/v2/docker_executor.py`, `patchforge/verification/tester.py`), assertion failure extraction (`patchforge/repair/assertion_failures.py`), and the iterative residual refinement loop (`patchforge/repair/replanner.py`).

---

## 2. Containerized Test Execution Forensics

### 2.1 Host Execution Ban & Container Boundary
Untrusted repository code must never be executed on the developer host machine. Tests frequently execute malicious shell commands, install uncontrolled dependencies, or access local files.

PatchForge enforces:
- `PATCHFORGE_DOCKER_COMMAND`: The explicit execution boundary.
- `DockerSelectedTestExecutor`: Executes tests exclusively within the pre-built SWE-bench evaluation container (e.g. `swebench/sweb.eval.x86_64.psf_1776_requests-2148:latest`).
- Read-only base images with isolated copy-on-write volumes.

### 2.2 The Reproducer Test Patch Injection (FIND-03)
In SWE-bench tasks, the base checkout lacks the reproducer test case (the test that proves the bug exists). 
- **The Defect:** In early V2 builds, `DockerSelectedTestExecutor` ran pytest against the raw checkout, resulting in pytest reporting `0 tests collected`.
- **The Solution:** The executor now accepts the task's `test_patch` and applies it inside the container before invoking the test runner:
```python
if self.test_patch:
    patch_cmd = f"git apply - << 'EOF'\n{self.test_patch}\nEOF"
    self._run_cmd(container, patch_cmd)
```
This guarantees that the container runs the true Fail-to-Pass verification suite.

### 2.3 Process Management: Hangs, Deadlocks & Timeouts
Python tests can hang indefinitely due to socket blocking, unmocked network requests, or infinite loops in generated code.
- `DockerSelectedTestExecutor` enforces a strict timeout (default 300s) on container sub-processes.
- If a timeout occurs:
  1. The container process is sent `SIGKILL`.
  2. `TestObservation(timed_out=True, return_code=-9)` is returned to the runner.
  3. The replanner classifies the candidate as `PERFORMANCE_HANG` and rejects it.

---

## 3. Assertion Extraction & Residual Refinement Forensics

### 3.1 Raw Output vs. Structured Assertion Extraction
Feeding 10,000 characters of raw pytest logs into an LLM prompt overwhelms the model with deprecation warnings, environment banners, and repeated traceback frames.

`AssertionFailureExtractor` transforms raw pytest logs into structured dataclasses:
```python
@dataclass
class AssertionFailure:
    test_id: str
    assertion_type: str
    expected: str
    actual: str
    message: str
    traceback: list[str]
```

### 3.2 Surgical Residual Feedback Construction
When a candidate achieves partial progress (e.g. passes 8 of 10 tests), the runner generates high-precision residual feedback:
```markdown
PARTIAL REPAIR PROGRESS DIAGNOSIS (Attempt 2):
- Succeeded on 8 of 10 required assertions (80.0% coverage).
- PRESERVE THESE PASSING ASSERTIONS (DO NOT REVERT OR REGRESS THEM):
    * test_requests.py::RequestsTestCase::test_DIGEST_AUTH_RETURNS_COOKIE
    * test_requests.py::RequestsTestCase::test_HTTP_200_OK_GET_ALTERNATIVE
    ...
- RESIDUAL FAILING ASSERTIONS (TARGET STRICTLY THESE REMAINING DEFECTS):
    * test_requests.py::RequestsTestCase::test_iter_content_handles_socket_error
- SMALLEST ACTIONABLE ASSERTION EVIDENCE:
    * test=test_requests.py::RequestsTestCase::test_iter_content_handles_socket_error
      assert isinstance(e, requests.exceptions.ConnectionError)
MANDATORY INSTRUCTION: Do NOT rewrite your implementation from scratch. 
Preserve your working logic and make ONLY the minimal targeted edit necessary.
```

### 3.3 The Anti-Thrashing Guard
Without explicit invariant locking, LLMs suffer from **repair thrashing**: fixing Test B while breaking Test A, then fixing Test A while breaking Test B. 
By explicitly providing the list of preserved passing assertions, the model treats existing logic as immutable constraints, stopping regression cycles.

---

## 4. Evaluation Isolation & Anti-Contamination

PatchForge maintains a strict boundary between candidate refinement and final benchmark evaluation:
1. **Zero Access to Gold Patches:** The repair runner, retriever, diagnoser, and prompt builder have no access to author gold patches or commit diffs.
2. **Offline Container Network Isolation:** Docker containers execute without internet access (`--network none` in standard evaluation), ensuring that repairs rely strictly on local repository logic.

---

## 5. Architectural Recommendations

1. **Standardize Test Selector Format:** Enforce pytest node ID syntax (`<file>::<class>::<test>`) across all adapters to prevent accidental full-suite invocations.
2. **Assertion Diff Rendering:** When expected vs actual values are strings or dicts, compute a line-by-line diff of the data structures in the feedback prompt to give the model immediate visual feedback on the exact discrepancy.
