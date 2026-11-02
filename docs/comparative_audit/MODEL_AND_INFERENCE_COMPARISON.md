# Model and Inference Comparison

PatchForge’s historical configuration is Qwen2.5-Coder 14B Q4 via Ollama. Its `OllamaProvider` has network timeouts and generation parameters, but the V1.9 artifacts do not establish effective context, prompt token count, KV/cache behavior, throughput or sampled-candidate budget. A model comparison cannot be inferred from systems typically evaluated with stronger hosted models, more command turns, or different costs.

SWE-agent source has explicit model instance cost accounting and retry budget handling (`RetryAgent._setup_agent`); its template config explicitly bounds observations. OpenHands performs asynchronous LLM/tool work and is designed for event/state projection. Agentless exposes model/backend, temperature and sample-count controls across localization/repair. These are **instrumentation/control advantages**, not demonstrations that their selected models are intrinsically better.

## Required comparable run manifest

Record: revision, dataset/harness/image, model+quantization, `num_ctx`, temperature/top-p, max output, system/prompt hashes, command/repair attempts, timeout, tokens, inference seconds, test seconds, hardware, candidate count, and all F2P/P2P IDs. Use it to separate model changes from tool-loop and editing changes.

## Decision

Keep the current provider abstraction. Add a capability manifest and local baseline instrumentation. Test a stronger/local or cloud model only after V2 can compare the same candidate/test lifecycle; otherwise an apparent gain will be confounded by prompt, context and iteration budget.
