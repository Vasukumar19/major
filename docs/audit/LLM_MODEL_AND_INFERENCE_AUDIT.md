# LLM Model and Inference Audit

## Evidence-based conclusion

V1.9 used `qwen2.5-coder:14b`, Q4_K_M, through the Ollama provider. Qwen2.5-Coder is a credible code-model family ([technical report](https://arxiv.org/abs/2409.12186)), but this audit found no controlled PatchForge model comparison at the current V1.9 configuration, no recorded effective context window, GPU utilization, TTFT, decode throughput, prompt-token accounting, or cost per success. Therefore “replace Qwen” is not an evidence-based next action.

The model client uses 300-second `urlopen` timeouts for Ollama generation (`models/provider.py:216, 292`) and passes output/temperature settings, but does not expose a measured `num_ctx` policy. Ollama documents that context can be configured globally or per API request ([context documentation](https://github.com/ollama/ollama/blob/main/docs/context-length.mdx)); PatchForge must log actual settings and prompt truncation before judging long-context performance.

## Model strategy

Use a provider-independent `ModelProvider` contract (already present) with capabilities declared per model: maximum tested context, structured-output reliability, tool-use protocol, price, privacy tier, and concurrency limit. Keep one primary repair model for the first experiments. Deterministic functions should do classification, safety checks, formatting, target containment, and acceptance.

Introduce a secondary cloud model only for a predeclared escalation cohort: failed local candidates with complete evidence and remaining budget. Do not split “planner” and “executor” by default; it doubles coordination and cost without evidence here.

| Option | Recommendation | Required evidence before adoption |
|---|---|---|
| Current Qwen 14B Q4/Ollama | Retain baseline | Capture context, tokens, latency and reproduction rate. |
| Larger local coding model | Pilot | Same prompts/cohort; compare resolve rate and cost/time. |
| Frontier hosted coding model | Opt-in escalation pilot | Privacy review, redaction, budget cap, comparable evaluation. |
| Separate reasoning model | Defer | Demonstrate diagnosis—not editing—is the limiting stage. |
| vLLM/SGLang | Defer migration | Measure multi-job contention and inference utilization first. |

vLLM documents continuous batching, chunked prefill and prefix caching ([documentation](https://docs.vllm.cc/en/latest/)); those are relevant only once simultaneous jobs and shared prompt prefixes are measured. Prefix caching also has tenant-isolation implications ([design](https://github.com/vllm-project/vllm/blob/main/docs/design/prefix_caching.md)).

## Controlled model evaluation

Freeze prompts, code revision, harness version, cohort and maximum attempts. Use 5-task smoke, targeted near-misses, then full 20-task run. Report resolved/F2P/P2P, valid-patch/harness rate, wall time, tokens, inference seconds, hardware, retries and cost per resolved task. Never use gold patches/test patches in prompts.
