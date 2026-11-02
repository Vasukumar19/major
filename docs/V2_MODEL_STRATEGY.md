# V2 Model Strategy

Service endpoints are configuration, not source constants. Set `PATCHFORGE_OLLAMA_URL` for an Ollama endpoint and optionally `PATCHFORGE_DOCKER_COMMAND` for a Docker binary. `RuntimeEnvironment` probes readiness and reports failures without launching services.

Model promotion requires identical task, prompt, attempt, selector and harness budgets plus recorded context, token, latency and cost. The current local Qwen configuration remains an unvalidated baseline, not V2 evidence.
