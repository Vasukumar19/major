# PatchForge v0.3: Architecture Specification

## 1. Executive Summary

PatchForge v0.3 evolves PatchForge from a rigid pipeline (`Localization -> Hypothesize -> Patch -> Verify`) into an **autonomous debugging agent**. Rather than guessing patches in isolation, PatchForge v0.3 navigates repositories using tools, validates hypotheses using diagnostic probes, pre-validates syntax with an AST gate, and treats execution results (tracebacks, assertion diffs, pass rates) as first-class evidence for iterative refinement.

---

## 2. System State Diagram

```text
                           Issue Description
                                   │
                                   ▼
                       ┌───────────────────────┐
                       │   Agent Controller    │
                       │ (State, Policy Guard) │
                       └───────────┬───────────┘
                                   │
                 ┌─────────────────┼─────────────────┐
                 ▼                 ▼                 ▼
          ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
          │    SEARCH    │  │     READ     │  │    GRAPH     │
          │ (grep/symbol)│  │ (file/tests) │  │  (call/AST)  │
          └──────┬───────┘  └──────┬───────┘  └──────┬───────┘
                 └─────────────────┼─────────────────┘
                                   ▼
                         ┌───────────────────┐
                         │  Evidence Store   │
                         └─────────┬─────────┘
                                   │
                                   ▼
                         ┌───────────────────┐
                         │   Understanding   │
                         │  (Delta/Invariants)
                         └─────────┬─────────┘
                                   │
                                   ▼
                         ┌───────────────────┐
                         │ Hypothesis Model  │
                         └─────────┬─────────┘
                                   │
                                   ▼
                         ┌───────────────────┐
                         │  Validation Probe │
                         │ (Static/Test/Repro│
                         └─────────┬─────────┘
                                   │
                     ┌─────────────┴─────────────┐
                     │                           │
                 Supported                  Weak/Wrong
                     │                           │
                     ▼                           ▼
            ┌─────────────────┐         ┌─────────────────┐
            │   Apply Patch   │         │ Re-Investigate  │
            │  (AST/Fuzzy)    │         │ (Alternative H) │
            └────────┬────────┘         └─────────────────┘
                     │
                     ▼
            ┌─────────────────┐
            │ Execute & Eval  │
            │ (Docker/Pytest) │
            └────────┬────────┘
                     │
          ┌──────────┴──────────┐
          │                     │
       Success              Near-Miss / Failure
          │                     │
          ▼                     ▼
        [DONE]         ┌─────────────────┐
                       │ExecutionEvidence│
                       │   Refinement    │
                       └────────┬────────┘
                                │
                                └───► Re-Patch / Probe
```

---

## 3. Core Subsystems

### 3.1 Tool Abstraction Layer (`patchforge/tools/`)
All actions available to the agent are encapsulated in discrete `Tool` implementations returning `ToolResult`:
- `search_code`: Regex/substring search across codebase with file path filters and match counts.
- `search_exact`: Exact literal matching.
- `find_symbol`: Definition lookup for functions, classes, and methods.
- `find_references`: Callers and usages of specific symbols.
- `read_file`: Windowed file view with start/end line bounds.
- `read_test`: Specialized extraction of relevant test cases.
- `inspect_graph`: Multi-tier code property graph querying (callers, callees, definitions).
- `inspect_git_history`: Git commit logs and blame for suspicious files.
- `run_targeted_test`: Execution of specific test files or test methods.
- `run_reproduction`: Local execution of reproduction scripts.
- `inspect_failure`: Parsing test outputs for failing assertions and stack traces.
- `apply_patch`: 3-tier safe fuzzy / exact / AST search-replace patching.
- `git_diff`: Inspection of uncommitted modifications.

### 3.2 Context Compactor (`patchforge/agent/context.py`)
Prevents unbounded context growth during long debugging sessions:
- Truncates repetitive search outputs.
- Keeps a prioritized Working Memory:
  - Issue description.
  - Active Program Understanding model.
  - Pinned key source files / snippets.
  - Active Hypothesis & Validation Probe results.
  - Latest Execution Evidence (clean tracebacks).
- Evicts outdated and unreferenced tool observations.

### 3.3 Program Understanding & Hypothesis Probing (`patchforge/reasoning/`)
- `ProgramUnderstanding`: Models the semantic delta:
  - `expected_behavior`: What the code was intended to do.
  - `observed_behavior`: What the bug exhibits.
  - `behavioral_delta`: The exact behavioral divergence.
  - `violated_invariants`: Contracts or preconditions broken.
- `Probe` Hierarchy:
  - `StaticProbe`: Verifies structural and typing assumptions in source files.
  - `TestProbe`: Checks existing test expectations against candidate fixes.
  - `ReproductionProbe`: Validates bug presence with minimal reproduction code.
  - `TracebackProbe`: Confirms stack trace alignments with hypothesized root causes.

### 3.4 RepoGraph Scalability Fallbacks
Implements three operational tiers:
1. `FULL_GRAPH`: For small/medium repositories (<100 files). Builds complete AST property graph.
2. `TARGETED_GRAPH`: For large repositories (Django, SymPy, Matplotlib). Builds localized graph on candidate files only.
3. `NO_GRAPH_FALLBACK`: Graceful fallback to regex/symbol search when graph indexing exceeds 15 seconds.

### 3.5 Execution-as-Evidence Feedback
When a test execution fails:
1. Pytest output is sanitized to extract failing test IDs, assertion differences, and stack traces.
2. Formats into `ExecutionEvidence` objects.
3. Enriches the agent's state to guide refinement without resetting localization.

---

## 4. Component Retention vs Replacement Matrix

| Component | v0.3 Decision | Justification |
|---|---|---|
| **SWE-bench Container Runner** | **KEEP** | Standardized evaluation environment. |
| **Agentless Localizer** | **KEEP + ADAPT** | Used as an initial discovery tool rather than a rigid stage. |
| **RepoGraph Graph Querying** | **KEEP + ADAPT** | Enhanced with fast targeted and fallback modes. |
| **3-Tier Fuzzy/AST Patch Engine** | **KEEP** | Proven robustness from v0.2. |
| **AST Pre-Validation Gate** | **KEEP** | Prevents uncompilable patches from reaching test containers. |
| **Near-Miss Retention** | **KEEP + EXTEND** | Refines high-pass-rate patches using execution diagnostics. |
| **Fixed Repair Loop** | **REPLACE** | Replaced with model-driven `AgentController`. |
| **Fixed Hypothesis Ranking** | **REPLACE** | Replaced with active `Probe` validation. |
