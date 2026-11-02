# PatchForge AI — Repair Synthesis & Verification Architecture

## 1. Competing Diagnosis & Hypothesis Generation

**Module**: `patchforge.diagnosis.diagnosis`

PatchForge avoids single-threaded speculative editing. When presented with a localized target and an issue description, the diagnosis engine produces multiple competing hypotheses:
- Each hypothesis (`DiagnosisHypothesis`) encapsulates:
  - `hypothesis_id`: Unique identifier.
  - `description`: Plain-English explanation of why the bug occurs.
  - `predicted_root_cause`: Specific code invariant violated.
  - `affected_components`: Target files and functions.
  - `confidence`: Calibrated probability score (0.0 to 1.0).
  - `causal_trace`: Directed sequence of execution events leading from trigger to symptom.

In V1.9, the hypothesis generation engine guarantees index and rank consistency: hypotheses are indexed contiguously starting from 0, and sorting preserves primary hypothesis selection stability.

---

## 2. Hypothesis Contract Gating

**Module**: `patchforge.diagnosis.contract_gate`

The `HypothesisContractGate` acts as a guardrail against speculative or ungrounded hypotheses:
- Evaluates API contracts (parameter types, default values, return invariants) extracted by `APIContract`.
- Builds a `BehavioralRequirementMatrix` comparing each hypothesis against known behavioral requirements.
- Computes counterfactual scores to evaluate whether a hypothesis introduces contradictions with existing contracts.

### V1.9 Conservative Selection Policy
Forensic analysis on V1.8 demonstrated that aggressive contract gating inverted selections, discarding correct primary diagnosis hypotheses in favor of speculatively scored counterfactual candidates.
In V1.9, the policy was corrected:
- The **Primary Diagnosis hypothesis is preserved by default**.
- It is overridden only if:
  1. An alternative candidate achieves a counterfactual score $\ge 0.80$.
  2. The primary hypothesis confidence is below the qualification threshold ($< 0.50$).

---

## 3. Repair Planning & Structured Unit Generation

**Modules**: `patchforge.repair.planner`, `patchforge.repair.structured_repair`, `patchforge.repair.schema`

### A. Repair Unit Types
PatchForge constrains the LLM to discrete, semantic repair actions:
1. `REPLACE_BLOCK`: Replace a contiguous block of statements (e.g. an `if/else` construct, loop, or method body).
2. `INSERT_BEFORE`: Inject statements immediately before an anchor statement.
3. `INSERT_AFTER`: Inject statements immediately after an anchor statement.
4. `WRAP_TRY_EXCEPT`: Fortify an expression with exception handling.

### B. Prompt Formulation & JSON Schema Enforcement
The `StructuredRepairPromptBuilder` constructs an LLM prompt containing:
- Target function source code with exact line numbers.
- Contract rules and behavioral expectations.
- Selected diagnosis hypothesis.
- Strict instructions to return a JSON array conforming to `RepairAction`:
  ```json
  [
    {
      "type": "REPLACE_BLOCK",
      "target_code": "if x is None:\n    return default",
      "replacement_code": "if x is None or x == '':\n    return default"
    }
  ]
  ```

---

## 4. Source Reconstruction & Static Validation

**Modules**: `patchforge.repair.reconstructor`, `patchforge.repair.validator`

### A. Surgical AST-Anchored Reconstruction (`SourceReconstructor`)
Rather than relying on LLM-generated diff headers or full-file rewrites:
1. `SourceReconstructor` locates the target file in memory.
2. It detects the target block using indentation-normalized line matching and AST span boundaries.
3. It performs surgical substring replacement, maintaining existing file indentation, formatting, docstrings, and comments.
4. If AST block matching fails due to slight token variations, it gracefully falls back to line-bounded string replacement.

### B. Static Syntax Validation (`StaticRepairValidator`)
Before initiating costly containerized test runs:
- The modified source is parsed using Python's `ast.parse()`.
- If a `SyntaxError` or `IndentationError` occurs:
  - The candidate patch is immediately rejected.
  - The syntax error message is captured for prompt refinement.
  - Zero Docker containers are spawned.

---

## 5. Docker Test Harness & Result Classification

**Modules**: `patchforge.integrations.swebench`, `patchforge.verification.classifier`, `patchforge.verification.tester`

### A. Containerized Execution
1. The validated patch is formatted as a unified git diff.
2. The diff is passed to `SWEBenchAdapter`, which runs the official SWE-bench evaluation container.
3. A 300-second execution timeout is enforced.
4. Tests are evaluated across two cohorts:
   - **FAIL_TO_PASS (F2P)**: Tests that originally failed due to the bug and must now pass.
   - **PASS_TO_PASS (P2P)**: Existing tests that must continue to pass (regression check).

### B. Failure Classification (`FailureClass`)
The output is classified into strict categories:
- `ALL_PASSED`: All F2P tests passed, and 0 P2P regressions occurred.
- `FAIL_TO_PASS_FAILURE`: F2P tests failed, with 0 P2P regressions.
- `PASS_TO_PASS_REGRESSION`: One or more previously passing tests regressed.
- `SYNTAX_ERROR`: Code failed during import or runtime compilation.
- `EXECUTION_TIMEOUT`: Test run exceeded 300 seconds.
- `WRONG_HYPOTHESIS`: 0 F2P tests passed, and execution trace indicates target was untouched or irrelevant.

---

## 6. Failure-Driven Replanning & Loop Breaking

**Modules**: `patchforge.repair.replanner`, `patchforge.memory.store`

The `FailureDrivenReplanner` inspects the `FailureClass` and historical attempts stored in `RepairMemoryStore`:
- **If `ALL_PASSED`**: Emits `ReplanAction.ACCEPT`.
- **If `FAIL_TO_PASS_FAILURE` and partial tests passed**: Emits `ReplanAction.RETRY` with focused residual error logs.
- **If `PASS_TO_PASS_REGRESSION`**: Rolls back the patch immediately and penalizes the candidate site.
- **If `WRONG_HYPOTHESIS`**: Switches to the next ranked hypothesis from the competing diagnosis set.
- **If attempt count $\ge 3$**: Emits `ReplanAction.ABORT` to prevent budget waste.
