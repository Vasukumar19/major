# Reference Systems and Repositories

| System | Classification | Source inspected | Language / license consideration | Comparable claim |
|---|---|---|---|---|
| [SWE-agent](https://github.com/SWE-agent/SWE-agent) | Autonomous issue-resolution agent | [`agents.py`](https://github.com/SWE-agent/SWE-agent/blob/main/sweagent/agent/agents.py), [`parsing.py`](https://github.com/SWE-agent/SWE-agent/blob/main/sweagent/tools/parsing.py) | Python; verify repository license before reuse | Direct architectural comparator; configurations/models/budgets differ. |
| [OpenHands SDK](https://github.com/OpenHands/software-agent-sdk) | General software-agent runtime | [`agent.py`](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/agent/agent.py), [`base.py`](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/agent/base.py), local conversation implementation | Python; verify license/version before reuse | Operational/runtime comparator, not a directly comparable benchmark repair policy. |
| [Agentless](https://github.com/OpenAutoCoder/Agentless) | Staged localization, repair, candidate validation | [`fl/localize.py`](https://github.com/OpenAutoCoder/Agentless/blob/main/agentless/fl/localize.py), [`repair/repair.py`](https://github.com/OpenAutoCoder/Agentless/blob/main/agentless/repair/repair.py) | Python; verify license before reuse | Direct repair-pipeline comparator; published models/budgets differ. |
| [Aider](https://github.com/Aider-AI/aider) | Interactive coding assistant | [`editblock_coder.py`](https://github.com/Aider-AI/aider/blob/main/aider/coders/editblock_coder.py), edit prompts | Python; verify license before reuse | Editing-protocol comparator, not autonomous SWE-bench agent. |
| [AutoCodeRover](https://github.com/AutoCodeRoverSG/auto-code-rover) | Repository-structure-aware APR agent | Official repository/paper documentation; source path/revision not pinned by this audit | Python; do not copy absent pinned source | Conceptual/source-discovery comparator only. |
| [SWE-bench](https://github.com/SWE-bench/SWE-bench) | Dataset and evaluation harness | PatchForge invokes `swebench.harness.run_evaluation` | Python; benchmark infrastructure | Reference evaluation adapter, not a repair agent. |
| SWE-smith / RepoCoder / historical OpenDevin | Training/retrieval/evolution context | Not source-pinned in this audit | Varies | Do not infer direct mechanisms or reuse. |

## Reproducibility warning

Headlines are incomparable without model, prompt, task split, retry/cost budget, environment and harness revision. Agentless’s repository describes a three-phase pipeline and reports particular configurations; these are not evidence that its number transfers to V1.9. SWE-agent explicitly supports configurable tools/models and retry strategies. Reference code should be vendored or pinned by commit only after a license/security review.

## Actual mechanisms read

* SWE-agent `RetryAgent._setup_agent` makes attempt directories, caps remaining cost, `hard_reset`s before a retry; `RetryAgent.run` saves trajectories and chooses after review. `TemplateConfig` has explicit truncation and shell-timeout messages.
* OpenHands `Agent.astep` executes LLM/tool work asynchronously; base-agent compatibility preserves tool availability on resume. Its conversation runtime is designed around persisted state/events.
* Agentless `localize_instance` progresses file -> related symbol -> fine-grained locations; `repair.process_loc` constructs bounded top-N code intervals, can sample generations, then parses multi-file commands.
* Aider applies search/replace blocks, reports no-match blocks with similar source lines and detects no-op replacement, so the model can repair its edit instruction.
