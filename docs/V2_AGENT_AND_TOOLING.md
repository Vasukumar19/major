# V2 Agent and Tooling

PatchForge already has `AgentController` plus search/read/symbol/reference/patch/test tools. The V2 decision is to reuse and harden it rather than recreate an agent loop. `scripts/run_task.py` now passes its tester into the agent registry and controller; this fixes a disconnected dependency, but does not yet make it the canonical V2 workflow.

The next promotion gate is a real task run with configured Ollama/Docker services that demonstrates tool observations, workspace containment and wrong-target recovery.

`patchforge.v2.agent_runner.V2AgentRunner` now bridges an AgentController-like
object into a V2-owned child workspace and registers a valid returned patch in
the candidate ledger. It is not yet the default cohort runner; promotion still
requires the live repair gate.
