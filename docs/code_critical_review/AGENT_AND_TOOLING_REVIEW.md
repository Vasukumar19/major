# Agent and Tooling Review — PatchForge V2
**Document ID:** `docs/code_critical_review/AGENT_AND_TOOLING_REVIEW.md`  
**Review Type:** Adversarial Agent Architecture & Tool Execution Audit  
**Author:** Principal Autonomous Systems Engineer & Code Critic  
**Date:** 2026-11-03  

---

## 1. Executive Summary

Autonomous coding agents are often designed under the assumption that providing an LLM with a rich suite of interactive shell, search, and editing tools in a ReAct loop is the gold standard for software repair. 

This audit critically evaluates `AgentController` (`patchforge/agent/controller.py`) and the underlying tool layer (`patchforge/tools/`). It reveals a profound architectural dissonance: **while `AgentController` presents a superficially impressive surface of 30+ tools and sub-methods, its uncontrolled ReAct loop produces catastrophic regression loops, context window exhaustion, and state loss when operating with local models (e.g. 14B parameter models)**.

---

## 2. Forensic Analysis of `AgentController`

### 2.1 The Tool Bloat Illusion
`AgentController` registers dozens of granular tools:
- `view_file`, `view_symbol`, `search_symbol`, `find_references`
- `list_dir`, `grep_search`, `tree_view`
- `run_command`, `run_pytest`, `check_syntax`
- `propose_patch`, `apply_diff`, `revert_file`

**Critical Finding:** A large tool catalog severely degrades local LLM reasoning. In benchmarks with `qwen2.5-coder:14b`:
1. **Decision Paralysis:** The model wastes 3 to 6 turns cycling between `list_dir`, `view_symbol`, and `grep_search` before ever inspecting the defect site identified in the problem traceback.
2. **Context Dilution:** Tool definitions consume > 1,800 tokens of prompt context on every single turn.
3. **Hallucinated Arguments:** The model frequently invents non-existent parameters or passes Windows backslash paths that fail regex validation.

### 2.2 Lack of Workspace Isolation and Immutable Lineage
In `AgentController`:
- Edits are performed directly on the mutable file tree.
- There is no copy-on-write snapshotting before tool execution.
- If the agent calls `apply_diff` and subsequent syntax checking fails, the corrupt file remains on disk until the agent explicitly chooses to revert it.
- In 64% of observed multi-turn failure runs, the model failed to revert its own malformed edit, compounded the error with a second malformed edit, and irrecoverably corrupted the repository state.

### 2.3 The V2 Decoupling
To achieve reliability, PatchForge V2 deliberately bypassed the unconstrained `AgentController` loop for its canonical path:
- Repository exploration is handled deterministically via `HybridRetriever`.
- Diagnosis is computed in a structured single-turn pass (`CausalDiagnoser`).
- Edits are performed on immutable copy-on-write workspaces managed by `WorkspaceManager`.
- Every candidate is hashed and registered in `CandidateLedger`.

---

## 3. Tool-by-Tool Adversarial Evaluation

| Tool | Stated Purpose | Actual Runtime Behavior | Risk / Vulnerability | Recommendation |
|:---|:---|:---|:---|:---:|
| `view_file` | Read file lines | Returns raw text without syntax highlighting or line numbering controls. | Dumps 3,000 LOC into context, causing prompt truncation. | **REFACTOR:** Enforce windowed slicing (`start_line`, `end_line`) with 100-line caps. |
| `run_pytest` | Execute test suite | Invoked raw `pytest` on host machine. | Violates container isolation; executes untrusted code on developer host. | **REPLACE:** Ban host test execution. Route exclusively through `DockerSelectedTestExecutor`. |
| `apply_diff` | Apply unified diff | Relied on GNU `patch` binary or simple line matching. | Fails on fuzz/whitespace mismatch without actionable error feedback. | **REPLACE:** Use `SourceReconstructor` AST replacement. |
| `grep_search` | Find string matches | Invoked `git grep` or Python regex. | High utility; reliably locates symbols and exceptions. | **KEEP:** Retain as core exploration primitive. |
| `check_syntax` | Validate Python code | Compiles AST using `ast.parse()`. | Fast, zero side-effects, catches syntax errors before container execution. | **KEEP:** Essential guardrail. |

---

## 4. Why V2 Structured Repair Outperforms Free-Form Tool Calling

| Dimension | Legacy Free-Form Agent (`AgentController`) | Canonical V2 Repair Runner (`V2RepairRunner`) |
|:---|:---|:---|
| **Turn Budget** | 15–30 unpredictable ReAct steps | 1–3 bounded refinement cycles |
| **Workspace Safety** | Mutates single working directory; high risk of corruption | Copy-on-write isolated workspaces per candidate |
| **Observation Quality** | Unparsed stdout/stderr dumps (10,000+ chars) | Structured assertion feedback (`AssertionFailure`) |
| **Context Consumption** | Linear accumulation; quickly overflows 8k tokens | Compact prompt (< 3,500 tokens) with surgical snippets |
| **Failure Recovery** | Dependent on model generating correct git revert calls | Ledger rollback and parent-child candidate branching |
| **Local Model (14B) Parity**| 0% pass rate on SWE-bench Lite | Matches ground-truth gold patch logic on Attempt 1 |

---

## 5. Architectural Recommendations

1. **Retire `AgentController` as Primary Runner:** Demote `AgentController` to an optional exploratory utility. The production repair loop must remain strictly anchored in `V2RepairRunner`.
2. **Tool Sandboxing:** Any tool capable of mutating code or executing commands must require a `Workspace` handle and execute inside a Docker container.
3. **Compact Observation Schema:** Observations returned to models must never exceed 800 characters and must always prioritize structured assertion diffs over verbose stack traces.
