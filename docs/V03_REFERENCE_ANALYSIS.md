# PatchForge v0.3: Reference Analysis of Open-Source Coding & Repair Agents

This document analyzes leading open-source software-engineering agents and repair systems across 13 core mechanisms (A–M) and defines the exact architectural adaptations implemented in **PatchForge v0.3**.

---

## 1. Systems Analyzed

1. **SWE-agent** (Princeton NLP): Interactive bash interface with customized ACI (Agent-Computer Interface) designed to prevent context overflow and hallucinated tool calls.
2. **mini-SWE-agent**: Minimalist, high-efficiency agent loop pairing standard prompt conventions with concise tool calling.
3. **OpenHands** (formerly OpenDevin): Multi-agent architecture with event streams, decoupled runtime sandbox, and stateful observation history.
4. **Cline / Roo-Code**: Structured tool calling with diff-based patch application and strict user/safety boundaries.
5. **Aider**: Git-backed multi-file editing with repository map (tree-sitter based symbol graphs) and fuzzy/exact search-replace block synthesis.
6. **Moatless Tools**: Evidence-driven code navigation using syntax trees, exact symbol definitions, and bounded search actions.
7. **Agentless**: Hierarchical localization (file -> function -> line) with two-phase generation and local test filtering.
8. **RepoGraph**: AST-based code property graph with call graphs, inheritance hierarchies, and cross-file reference links.

---

## 2. Comparative Analysis Across Mechanisms (A–M)

| Mechanism | Open-Source Implementations & Patterns | Observed Failure Modes / Limitations | PatchForge v0.3 Adaptation |
|---|---|---|---|
| **A. Investigation & Search Strategy** | SWE-agent (find/grep), Moatless (code snippet search), Aider (repo map rank) | Unbounded search dumps large token buffers; generic grep misses renamed symbols | **Multi-tier search**: Exact literal search, token grep, and AST symbol lookup with bounded result limits (10 matches max per call). |
| **B. File Navigation & Context Management** | SWE-agent (paged view 100 lines), Aider (compact repo map), OpenHands (file reader) | Dumping entire large files saturates LLM context; repeated reads cause context bloat | **Context Compactor (`patchforge/agent/context.py`)**: Windowed file views (100 lines + context), deduplicating search results, and maintaining a compact Working Memory. |
| **C. State Representation & Persistence** | OpenHands (EventStream), mini-swe-agent (History list), Agentless (JSON records) | Unstructured conversation histories make backtracking and state-based reflection impossible | **Serializable `AgentState`**: Tracks visited files, symbol anchors, active evidence, current hypothesis, validation probe outcomes, and patch attempts. |
| **D. Action Space & Tool Abstraction** | SWE-agent (bash/custom tools), Moatless (`Action`/`ToolCall`), Cline (JSON schema) | Direct bash execution causes syntax errors, hangs, or shell injection | **Structured `Tool` & `ToolResult` interfaces**: 13 discrete typed tools with validated JSON schemas and structured outputs (`status`, `data`, `message`). |
| **E. Error Recovery & Dynamic Pivot** | OpenHands (retry loop), SWE-agent (error messages in prompt) | Rigid pipelines fail permanently if first step fails; unguided retries loop identically | **Dynamic State Navigation**: On probe/patch failure, agent transitions back to `INVESTIGATE` or `REFINE` rather than executing rigid sequential stages. |
| **F. Hypothesis Formation & Reasoning** | PatchForge v0.1/v0.2 (3 competing hypotheses), Agentless (repair candidates) | Static 3-hypothesis prompts without runtime behavioral delta produce superficial guesses | **Structured Program Understanding**: Explicitly derives `expected_behavior`, `observed_behavior`, `behavioral_delta`, and `violated_invariants`. |
| **G. Hypothesis Validation & Probing** | Scientific debugging (Zeller), Moatless (syntax checking), PatchForge v0.2 (ranking) | LLM ranks hypotheses based only on prompt text without testing ground truth | **Validation Probes (`Probe` / `ProbeResult`)**: `StaticProbe`, `TestProbe`, `ReproductionProbe`, and `TracebackProbe` test predictions before code edits. |
| **H. Patch Generation & Synthesis** | Aider (SEARCH/REPLACE blocks), Agentless (function rewrite), Cline (diff blocks) | Full-file rewrites truncate files; single-line replacements fail on indentation mismatches | **3-Tier Robust Patch Engine**: Preserves Exact -> Safe Multi-Tier Fuzzy -> AST Node Replacement with ambiguity rejection. |
| **I. Patch Validation & AST/Syntax Gates** | PatchForge v0.2 (ast.parse gate), Aider (tree-sitter linter) | Invalid syntax sent to Docker containers wastes 60–120s per test execution | **AST Pre-Validation Gate**: Fast local syntax and AST validation before any container execution, rejecting broken patches in <5ms. |
| **J. Execution-Driven Feedback & Refinement** | Aider (pytest output to LLM), SWE-agent (bash exit code + stdout) | Feeding raw 1000-line pytest dumps causes context overflow and hallucinated fixes | **Execution-as-Evidence**: Extracts clean failure tracebacks, assertion diffs, and failing test names into structured `ExecutionEvidence` for refinement. |
| **K. Repository Graph & Structural Navigation** | RepoGraph (AST graph), Aider (RepoMap/tree-sitter) | Whole-repo graph construction hangs/times out on large codebases (Django, SymPy >1200s) | **3-Tier Graph Scalability**: `FULL_GRAPH` (small repos) -> `TARGETED_GRAPH` (coarse local files) -> `NO_GRAPH_FALLBACK` (graceful symbol search). |
| **L. Budgeting & Termination Policy** | mini-swe-agent (max_turns), OpenHands (budget limits) | Agents get trapped in endless search loops or exhaust API credits | **Deterministic Policy & Budget Guard**: Strict turn budget (15 turns max), tool invocation caps, token limits, and deterministic stop criteria. |
| **M. Benchmark & Evaluation Integration** | SWE-bench harness, Agentless eval | Leaking gold patches or modifying test patches invalidates benchmark integrity | **Zero-Leakage SWE-bench Harness**: Evaluates generated patches exclusively against SWE-bench Docker containers without inspecting gold patches. |

---

## 3. Key Design Takeaways for PatchForge v0.3

1. **Autonomous Debugging Agent over Pipeline**: Move away from a one-pass pipeline (`Localize -> Hypothesize -> Patch -> Verify`). Enable the agent to choose actions dynamically based on tool observations.
2. **Deterministic Boundaries, Model-Driven Decisions**: Enforce strict safety, turn limits, and budget guards deterministically, while allowing the model to decide whether to search, read, probe, or patch.
3. **Execution as Primary Reasoning Input**: When a patch or probe fails, the resulting execution trace is immediately converted to structured evidence, allowing the agent to refine its hypothesis or investigate alternate root causes.
4. **Context Compaction as First-Class Feature**: Prune redundant search results and truncate lengthy tool outputs to maintain clean, focused context for the LLM.
5. **Graph Resilience**: Never allow graph indexing failure to block task resolution.
