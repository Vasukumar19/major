# PatchForge AI — Execution Flow Walkthrough

This document traces the complete execution lifecycle of a single bug report as it moves through the PatchForge AI V1.9 repair pipeline.

---

## Example Scenario: Defect Lifecycle

Consider an issue report submitted for an open-source HTTP library:
> **Issue**: `Session.send() raises TypeError when stream=None is explicitly passed.`
> **Expected**: `stream=None` should default to `False` rather than raising `TypeError`.

---

### Step 1: Work Item Ingestion & Path Traversal Validation
**Module**: `patchforge.integrations.work_item` / `patchforge.integrations.github`

1. The issue payload arrives (via GitHub Webhook, direct API call, or SWE-bench loader).
2. The payload is parsed into a `PatchForgeWorkItem`:
   ```python
   work_item = PatchForgeWorkItem(
       instance_id="psf__requests-example",
       repo="psf/requests",
       base_commit="da39a3ee5e6b4b0d3255bfef95601890afd80709",
       problem_statement="Session.send() raises TypeError when stream=None...",
       hints_text="",
   )
   ```
3. `_validate_safe_path` validates all repository paths, ensuring no directory escapes (`..`) or absolute paths are accepted.

---

### Step 2: Repository Indexing & Code Graph Construction
**Module**: `patchforge.repository_intelligence.indexer` & `graph`

1. `RepositoryIndexer` traverses `psf/requests` and parses all Python source files using `ast.parse`.
2. Symbols are extracted: classes (`Session`, `HTTPAdapter`), functions (`send`, `request`, `build_response`), and docstrings.
3. `RepositoryGraph` builds a directed `networkx` graph containing:
   - Call edges: `Session.request` → `Session.send` → `HTTPAdapter.send`
   - Defines edges: `Session` → `Session.send`
   - Import edges: `sessions.py` → `adapters.py`
4. `TestExtractor` parses test suites (`tests/test_requests.py`), identifying test functions and assertions that exercise `Session.send`.

---

### Step 3: Hybrid Retrieval & Candidate Localization
**Module**: `patchforge.retrieval.hybrid`

1. `HybridRepositoryRetriever` executes multi-factor scoring for candidate files and symbols:
   - **Lexical BM25**: Matches keywords (`Session`, `send`, `stream`, `TypeError`).
   - **AST Relevance**: Prioritizes `Session.send` in `requests/sessions.py` and `HTTPAdapter.send` in `requests/adapters.py`.
   - **Test Correlation**: Notes that tests exercising `stream` parameters target `sessions.py`.
2. Output: A ranked list of `RetrievedCandidate` locations with line boundaries:
   - `requests/sessions.py:Session.send` (Lines 580–650)
   - `requests/adapters.py:HTTPAdapter.send` (Lines 410–480)

---

### Step 4: Competing Diagnosis & Hypothesis Generation
**Module**: `patchforge.diagnosis.diagnosis`

1. The diagnosis engine evaluates the error symptom (`TypeError: stream must be bool, got NoneType`).
2. Two competing hypotheses are formulated:
   - **Hypothesis 0 (Confidence 0.85)**: `Session.send` does not normalize `stream=None` before dispatching to the transport adapter.
   - **Hypothesis 1 (Confidence 0.60)**: `HTTPAdapter.send` should accept `None` and treat it as `False`.
3. Causal predictions and behavioral traces are recorded for each hypothesis.

---

### Step 5: Contract Gate & Behavioral Requirements
**Module**: `patchforge.diagnosis.contract_gate`

1. `HypothesisContractGate` evaluates parameter contracts and behavioral invariants.
2. In V1.9, the Contract Gate checks whether counterfactual evidence requires overriding Hypothesis 0.
3. Counterfactual score for Hypothesis 1 is `0.45` (< `0.80` threshold).
4. **Decision**: Preserves Hypothesis 0 (`Session.send` normalization) as the primary target.

---

### Step 6: Qualified Candidate Ranking
**Module**: `patchforge.repair.ranking`

1. Candidate sites are indexed using fully qualified profile identities:
   - `requests/sessions.py::Session::send::action_target`
   - `requests/adapters.py::HTTPAdapter::send::action_target`
2. `RepairSiteRanker` sorts candidates deterministically, placing `requests/sessions.py::Session::send` at Rank 1.

---

### Step 7: Structured Repair Synthesis
**Module**: `patchforge.repair.structured_repair` & `patchforge.models.provider`

1. `StructuredRepairPromptBuilder` constructs an LLM prompt containing:
   - Problem statement and expected behavior.
   - Target code span (`Session.send` body).
   - API contract (`stream: bool | None`).
   - Strict instructions to return a JSON array conforming to the `RepairAction` schema.
2. The prompt is dispatched to `qwen2.5-coder:14b` via `OllamaProvider`.
3. The LLM returns a structured JSON response:
   ```json
   [
     {
       "type": "INSERT_BEFORE",
       "target_code": "adapter = self.get_adapter(url=request.url)",
       "replacement_code": "if stream is None:\n    stream = False"
     }
   ]
   ```
4. `StructuredRepairParser` strips markdown markers and parses the array into `RepairAction` objects.

---

### Step 8: AST-Anchored Patch Reconstruction
**Module**: `patchforge.repair.reconstructor` & `validator`

1. `SourceReconstructor` locates `Session.send` in `requests/sessions.py`.
2. Locates the exact line `adapter = self.get_adapter(url=request.url)` matching target indentation.
3. Inserts the replacement statement before the target line, preserving surrounding comments.
4. `StaticRepairValidator` runs `ast.parse()` on the modified file:
   - If syntax is invalid, the candidate is discarded immediately.
   - If syntax is valid, a unified git diff is generated.

---

### Step 9: Docker Harness Evaluation
**Module**: `patchforge.integrations.swebench` / `patchforge.verification.tester`

1. The unified git diff is mounted into the isolated SWE-bench Docker container for `psf/requests`.
2. The container applies the patch using `git apply`.
3. The harness runs the FAIL_TO_PASS (F2P) test suite and PASS_TO_PASS (P2P) test suite.
4. Results:
   - F2P: 1/1 passed.
   - P2P: 65/65 passed (0 regressions).
   - Exit code: 0.

---

### Step 10: Classification & Resolution
**Module**: `patchforge.verification.classifier` & `patchforge.repair.replanner`

1. `FailureClass` evaluates test output: `ALL_PASSED`.
2. `FailureDrivenReplanner` issues decision: `ReplanAction.ACCEPT` ("All F2P passed with 0 regressions").
3. `V1RepairOrchestrator` finalizes the task:
   - Writes task artifact JSON to `results/v19_orchestrator/psf__requests-example.json`.
   - Records timing, token usage, and git diff.
   - Updates cohort summary metrics.
