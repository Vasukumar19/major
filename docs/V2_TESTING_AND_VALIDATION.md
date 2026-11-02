# V2 Testing and Validation

Focused foundation validation, including the assertion evidence path, produced **83 passed** across the V2, runtime, provider, tool, and residual-refinement tests.

The complete offline suite produced **340 passed**. The runner's default system temporary directory is unavailable in this remote container, so the suite was executed in three bounded groups with `TEMP` and `TMP` directed to a repository-owned temporary directory. This does not start Docker or Ollama.

This proves workspace containment, candidate lineage/selection, explicit runtime configuration, selected-tool behavior, and assertion-driven residual guidance. It does not prove autonomous repair improvement. The next required evidence is a controlled near-miss pilot with live Ollama/Docker; the current environment cannot reach either service.
