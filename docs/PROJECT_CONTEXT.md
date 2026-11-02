# PatchForge AI — Project Context & Engineering Philosophy

## 1. Why PatchForge AI Was Created

Large language models (LLMs) have demonstrated impressive code-generation capabilities, but applying them to **real-world software maintenance** (such as issues in popular open-source repositories) exposes severe failure modes when using naive unconstrained conversational agents:

1. **Context Pollution & Hallucination**: Chat-based agents (e.g. standard multi-turn ReAct loops) quickly saturate context windows with thousands of lines of irrelevant code, test logs, and conversation history, degrading reasoning accuracy.
2. **Ungrounded Guesses**: Models hypothesize root causes without verifying API contracts, variable definitions, or call hierarchies, frequently editing unrelated caller/callee functions or introducing regressions into passing tests.
3. **Patch Application Fragility**: Generating entire source files or freeform diff strings results in indentation errors, lost comments, or malformed unidiffs that cannot be cleanly applied by `git apply` or `patch`.
4. **Regressive Loops**: When a test fails after patch generation, unconstrained agents often repeat identical ineffective edits or thrash between different non-functional variants without learning from previous execution traces.

**PatchForge AI** was created to address these deficiencies by treating Automated Program Repair (APR) as a **disciplined, evidence-driven engineering pipeline** rather than an open-ended conversational trial-and-error loop.

---

## 2. Core Objectives

PatchForge AI is designed around four primary objectives:

1. **Deterministic Grounding**: Every candidate repair target, diagnosis hypothesis, and patch must be grounded in static code graphs, AST symbol hierarchies, temporal commit churn, or observed test failure traces.
2. **Surgical Precision**: Modifications must be scoped to bounded AST spans (`REPLACE_BLOCK`, `INSERT_BEFORE`, `INSERT_AFTER`, `WRAP_TRY_EXCEPT`) and reconstructed back into source code without disturbing unrelated formatting, comments, or docstrings.
3. **Regression Safety (P2P Preservation)**: A proposed fix must never trade regression of passing tests (PASS_TO_PASS) for resolution of failing tests (FAIL_TO_PASS). In V1.9, PatchForge achieved a 97.0% P2P pass rate across 1,115 test cases.
4. **Reproducibility & Empirical Evaluation**: Every decision, hypothesis, prompt, token count, test execution, and patch diff is persisted in structured JSON artifacts for longitudinal comparison across model and architecture versions.

---

## 3. Engineering Scope & Current Focus

### In Scope
- **Python Repositories**: Deep static AST analysis, control-flow modeling, symbol indexing, and repair synthesis tailored for Python 3 codebases.
- **SWE-bench Evaluation**: Direct integration with the official SWE-bench Docker evaluation harness on real-world repositories (e.g. `pallets/flask`, `psf/requests`, `pylint-dev/pylint`, `pytest-dev/pytest`).
- **Local Small-to-Mid Sized LLMs**: Evaluation primarily driven by open-weight local code models (specifically `qwen2.5-coder:14b` running via local Ollama) rather than expensive cloud APIs.
- **Normalized Ingestion**: Standardized work item representation supporting SWE-bench datasets, raw GitHub webhooks with HMAC validation, and direct JSON ingestion.

### Explicit Non-Goals
- **Non-Python Languages**: PatchForge does not currently support Java, C++, TypeScript, or Go repository analysis or AST reconstruction.
- **General Interactive Chatbot**: PatchForge is not an interactive conversational IDE plugin or multi-purpose programming tutor. It is an automated repair engine that ingests an issue and produces a verified git diff.
- **Multi-File Architectural Refactoring**: PatchForge is optimized for defect repair, bug fixes, and behavioral compliance within bounded edit sites. It does not perform system-wide architectural restructuring or mass renaming.
- **Third-Party SaaS Integrations**: PatchForge does not maintain bespoke cloud connectors for Jira, Linear, Sentry, GitLab, or PagerDuty. All external ingestion is normalized into `PatchForgeWorkItem`.

---

## 4. Key Architectural Decisions

### A. Pipeline Architecture over Agentic Free-for-All
Early versions (V0.2/V0.3) explored multi-turn agentic controller loops (`AgentController`) where the LLM decided which tools to invoke at each step. Benchmark evidence proved that unconstrained tool selection frequently led to loops, tool invocation syntax failures, and budget exhaustion.
The canonical V1 architecture (`V1RepairOrchestrator`) enforces a deterministic sequence:
`Ingestion → Indexing → Retrieval → Diagnosis → Contract Gate → Qualified Ranking → Structured Repair → AST Reconstruction → Static Validation → Docker Test → Replanning`.

### B. Structured JSON Repair Actions over Freeform Diffs
Rather than asking LLMs to generate unified diff strings (`--- a/file.py +++ b/file.py@@ ... @@`), PatchForge prompts the model for structured JSON schemas describing semantic actions:
- `REPLACE_BLOCK`: Replace a specific function or block body.
- `INSERT_BEFORE` / `INSERT_AFTER`: Surgical statement injection.
- `WRAP_TRY_EXCEPT`: Exception handling fortification.
The `SourceReconstructor` then performs AST-guided surgical replacement in the original file, guaranteeing syntactic coherence.

### C. Qualified Target Identification
In V1.8, target sites were identified by unqualified names (e.g. `send`), which caused candidate profile collapse when multiple files contained functions with identical names (e.g., `requests/adapters.py:send` vs `requests/sessions.py:send`). V1.9 introduced fully qualified identities:
`file_path::class_name::function_name::action_target`
This guarantees zero identity collision across modules.

### D. Conservative Contract Gate Policy
Forensics on the V1.8 regression revealed that an aggressive contract gate was discarding accurate diagnosis hypotheses in favor of speculatively scored counterfactual candidates. V1.9 established a conservative policy: the Primary Diagnosis hypothesis is preserved unless counterfactual evidence exceeds an empirical threshold (score > 0.8) and meets confidence requirements.

---

## 5. Long-Term Vision

The ultimate trajectory for PatchForge AI is to serve as an **autonomous, verifiable software maintenance worker**:
- Capable of running locally on developer workstations or in enterprise CI/CD pipelines.
- Verifying incoming bug reports against unit and integration tests.
- Proposing verified, minimal, zero-regression patches with complete formal evidence and explanation before a human engineer ever reviews the pull request.
