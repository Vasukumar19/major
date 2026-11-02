# Mechanism Adoption Matrix

| Proven system | Mechanism / source | PatchForge equivalent | Adaptation | Benefit / complexity | Status |
|---|---|---|---|---|---|
| SWE-agent | [`RetryAgent.run/_next_attempt`](https://github.com/SWE-agent/SWE-agent/blob/main/sweagent/agent/agents.py) saves trajectories, reviews retries and hard-resets env | `FailureDrivenReplanner`, mutable checkout | isolated attempt workspace + persisted candidate/event ledger | high / medium | Adopt concept, not code. |
| SWE-agent | [`TemplateConfig`](https://github.com/SWE-agent/SWE-agent/blob/main/sweagent/agent/agents.py) explicit observation clipping/error templates | one large assembled prompt | typed compact observations and token budget | high / low | Adopt. |
| SWE-agent | [`ThoughtActionParser`](https://github.com/SWE-agent/SWE-agent/blob/main/sweagent/tools/parsing.py) reparses/feeds action errors | structured JSON parser | tool/action error as next-turn observation | high / low | Adopt. |
| Agentless | [`localize_instance`](https://github.com/OpenAutoCoder/Agentless/blob/main/agentless/fl/localize.py) hierarchical file->symbol->line localization | hybrid precomputed ranking | staged localization ablation, retain deterministic index | medium / medium | Experiment. |
| Agentless | [`repair.py`](https://github.com/OpenAutoCoder/Agentless/blob/main/agentless/repair/repair.py) candidate sampling, bounded intervals and multifile parsing | single structured candidate, multi-edit reconstructor | bounded N-candidate set with identical test budget | high / medium | Pilot. |
| Aider | [`EditBlockCoder.apply_edits`](https://github.com/Aider-AI/aider/blob/main/aider/coders/editblock_coder.py) detailed SEARCH failure feedback | AST/line reconstruction failures | alternate search/replace protocol plus diagnostics | high for pre-harness defects / low | Pilot. |
| OpenHands | [`Agent.astep`](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/agent/agent.py) and conversation runtime | synchronous monolith | append-only events, cancellation/resume boundaries | high production value / high | Adapt architectural principle. |
| AutoCodeRover | structural search + optional SBFL ([paper](https://arxiv.org/abs/2404.05427)) | AST graph/test index without coverage | SBFL only if targeted test reproduction works | possible / medium-high | Experiment. |

## Avoid

Do not copy an entire agent framework, import reference source without a license/security review, deploy multi-agent roles by default, add a vector database, or migrate serving before a measured bottleneck. Do not treat Aider’s interactive assumptions as an autonomous acceptance policy.

## Retain from PatchForge

Normalized work items and webhook validation, provider abstraction, source/test indexing, structured repair contract, official harness integration, static validation, and raw result persistence remain useful foundations.
