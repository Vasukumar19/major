# V2 Known Limitations

* No configured reachable Ollama or Docker service was available during this implementation, so no real SWE-bench pilot was run.
* V2 currently persists candidate events locally as JSONL, not a durable multi-worker service.
* The selected-test contract exists, but a SWE-bench task-container selector executor remains to be implemented.
* The V2 agent runner has not yet replaced V1 canonical orchestration.
* A pre-existing broader-suite cache invalidation test failed in this environment; it is outside the V2 foundation changes and must be investigated separately.
