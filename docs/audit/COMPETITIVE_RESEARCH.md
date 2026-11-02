# Competitive and Research Review

## External findings (separate from PatchForge recommendations)

* [SWE-agent](https://github.com/swe-agent/swe-agent) is a tool-using issue-to-patch agent and its maintainers now recommend their simpler Mini-SWE-Agent direction for many uses. The transferable lesson is not “use its benchmark score,” but that a small, reliable tool surface and real execution feedback can outperform elaborate coordination.
* [Agentless](https://arxiv.org/abs/2407.01489) separates localization, repair generation and validation rather than requiring an unconstrained conversational agent. This supports PatchForge’s deterministic-stage instinct.
* [OpenHands](https://github.com/OpenHands/OpenHands/blob/main/README.md?plain=1) separates user interface, agent server and automation/runtime services. It is relevant to future operational boundaries, not a justification to adopt its entire stack.
* The [SWE-bench repository](https://github.com/SWE-bench/SWE-bench/blob/main/README.md?plain=1) is the appropriate harness/data reference; aggregate claims from incomparable configurations must not be used as direct PatchForge performance evidence.

## PatchForge comparison

| Area | Existing PatchForge | Adopt / avoid |
|---|---|---|
| Localization | AST/test/heuristic hybrid | Adopt ablation discipline; avoid claiming vector/RRF without implementation. |
| Agent control | Fixed staged pipeline | Retain; avoid adding multiple agents before state isolation. |
| Editing | Structured schema + source reconstruction | Retain schema; replace fragile mutation implementation selectively. |
| Execution | Official harness adapter | Retain; add incremental/typed test observations. |
| Production | Local files/checkouts | Adopt service/workspace separation, not a full framework rewrite. |

## Independent recommendations

PatchForge’s defensible differentiation is a strict structured-repair boundary and evidence artifact collection. It is not yet demonstrated that temporal graphs, counterfactual scoring, or “hybrid dense retrieval” improve repair. Stop rebuilding general agent frameworks; borrow narrow, proven ideas: controlled tool execution, separate workspaces, concise test feedback, and reproducible trajectories.
