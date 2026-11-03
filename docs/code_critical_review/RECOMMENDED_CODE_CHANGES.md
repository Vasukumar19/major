# Recommended Code Changes — PatchForge V2
**Document ID:** `docs/code_critical_review/RECOMMENDED_CODE_CHANGES.md`  
**Review Type:** Goal-Driven Engineering Action Plan & Component Disposal Matrix  
**Author:** Principal Autonomous Systems Engineer & Code Critic  
**Date:** 2026-11-03  

---

## 1. Executive Summary

This document establishes the definitive code change matrix for transitioning PatchForge into an autonomous, industrial-grade software repair engine. 

Every component is assigned an unambiguous verdict:
- **KEEP:** Correct implementation; preserve as-is.
- **FIX:** Correct implementation defect while preserving interface.
- **CONNECT:** Bridge disconnected capability to canonical runner.
- **REFACTOR:** Improve modularity, signature, or state flow.
- **REPLACE:** Replace fundamentally unsuitable implementation.
- **REMOVE:** Purge obsolete or harmful complexity.
- **EXPERIMENT:** Measure benefit in controlled isolation before merging.

---

## 2. Master Component Action Matrix

| Component / Subsystem | Primary File(s) | Current Status | Action | Engineering Rationale & Implementation Scope |
|:---|:---|:---:|:---:|:---|
| **V2 Canonical Runner** | `patchforge/v2/runner.py` | Active | **KEEP** | Core orchestration engine. Owns workspace lifecycle, candidate ledger, and Docker verification. |
| **Workspace Manager** | `patchforge/v2/workspace.py` | Active | **KEEP** | Provides isolated, copy-on-write workspaces per candidate attempt. |
| **Candidate Ledger** | `patchforge/v2/candidates.py` | Active | **KEEP** | Append-only DAG of candidate records and test observations; eliminates state loss. |
| **Docker Selected Test Executor** | `patchforge/v2/docker_executor.py` | Active | **FIX** | *Fixed FIND-03:* Inject `test_patch` into container before running pytest selectors. |
| **Source Reconstructor** | `patchforge/repair/reconstructor.py` | Active | **FIX** | *Fixed FIND-01:* Prevent built-in Python 3 `ConnectionError` shadowing; auto-heal relative exception imports. |
| **SWE-bench Adapter** | `patchforge/integrations/swebench.py` | Active | **FIX** | *Fixed FIND-02:* Enforce valid JSON Lines serialization for `.jsonl` files in `write_predictions()`. |
| **Site Ranker** | `patchforge/repair/ranking.py` | Active | **FIX** | *Fixed FIND-06:* Add +250.0 bonus for innermost traceback execution frames. |
| **Structured Repair Prompts** | `patchforge/repair/structured_repair.py`| Active | **FIX** | *Fixed FIND-07:* Add Tier 3 raw Python replacement fallback when JSON AST parsing fails. |
| **Replanning State Machine** | `patchforge/v2/runner.py`<br>`patchforge/repair/replanner.py` | Active | **FIX** | *Fixed FIND-08:* Transition runner to `use_replacement_only = True` on `FALLBACK_SYNTAX`. |
| **Ollama Model Provider** | `patchforge/models/provider.py` | Active | **FIX** | *Fixed FIND-10:* Expose `PATCHFORGE_NUM_CTX` and `PATCHFORGE_OLLAMA_TIMEOUT`. |
| **Assertion Failure Extractor** | `patchforge/repair/assertion_failures.py`| Active | **KEEP** | Extracts structured test failure objects from raw pytest logs. |
| **Hybrid Retriever** | `patchforge/retrieval/hybrid.py` | Active | **REFACTOR**| Streamline retrieval: default to BM25 + AST symbol declarations; remove dead weight. |
| **Agent Controller** | `patchforge/agent/controller.py` | Disconnected | **REPLACE**| Free-form ReAct loop is unsuitable for 14B models. Demote to optional exploratory utility. |
| **Legacy V1 Orchestrator** | `patchforge/pipeline/v1_orchestrator.py` | Legacy | **REMOVE** | Contains the destructive reset-and-rebuild bug. Retain only for historical compatibility. |
| **Temporal Git Mining** | `patchforge/diagnosis/temporal.py` | Experimental | **REMOVE** | 20s disk walk overhead with 0% measurable localization uplift on SWE-bench tasks. |
| **Static Code Graph** | `patchforge/retrieval/code_graph.py` | Experimental | **REMOVE** | High memory usage (500MB+) and poor dynamic Python edge resolution. |
| **Policy Guards** | `patchforge/agent/policy_guards.py` | Redundant | **REMOVE** | Duplicated by V2 candidate acceptance policies (`accepts_residual_candidate()`). |
| **Dynamic Execution Experiments** | `patchforge/repair/dynamic.py` | Dead Code | **REMOVE** | Unused experimental file with zero callers in canonical V2 path. |

---

## 3. High-Priority Immediate Code Changes (Top 5)

### Action 1: Remove Destructive V1 Invocations from Testing Suite
- **Target:** `tests/test_v1_orchestrator.py`
- **Scope:** Ensure no tests invoke `git checkout .` on the parent working tree. Mark all V1 tests with `@pytest.mark.legacy`.

### Action 2: Purge Heavyweight Graph Dependencies from Default Imports
- **Target:** `patchforge/retrieval/__init__.py`
- **Scope:** Lazy-load `networkx` only if explicitly requested. Eliminate 300MB import overhead during basic CLI invocations.

### Action 3: Formalize Search/Replace Single-Line Edits
- **Target:** `patchforge/repair/reconstructor.py`
- **Scope:** When an edit modifies $\le 3$ lines, support Aider-style Search/Replace splicing directly into AST without regenerating full method bodies.

### Action 4: Automated Container Workspace Pruning
- **Target:** `patchforge/v2/workspace.py`
- **Scope:** Add `workspace.cleanup(keep_best=True)` to delete unpromoted intermediate workspaces from disk, preserving disk space during large batch runs.

### Action 5: Dynamic Context Pruning in Problem Ingestion
- **Target:** `patchforge/issue/problem.py`
- **Scope:** Automatically strip CI/CD log noise (e.g. `[INFO] Downloading...`, GitHub Actions banners) from issue text before passing to LLM prompts.
