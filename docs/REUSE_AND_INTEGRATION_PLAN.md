# PatchForge — Reuse and Integration Plan (Phase 1)

**Status:** Phase A — Discovery (clean restart, no legacy code preserved)
**Date:** 2026-09-06
**Workspace:** `C:\Users\kumar\project\major project` (was EMPTY — 0 entries, not a git repo)
**Principle:** REUSE EXISTING SYSTEMS + ADD PATCHFORGE RESEARCH LAYER = LOW-COST, EVIDENCE-DRIVEN REPAIR

> Previous PatchForge implementation: NOT FOUND in workspace. Nothing to preserve.
> Decision: clean `patchforge/` package (to be created in Phase C only after baselines work).
> `third_party/` is read-only upstream. All PatchForge-specific code lives in `patchforge/integrations/` adapters.

---

## 0. Workspace inspection result

| Check | Result |
|---|---|
| Workspace contents | EMPTY (no old PatchForge, no docs, no code) |
| Git | `git 2.49.0` available, directory NOT a git repo |
| Python / pip | 3.12.10 / 26.1.2 |
| Docker | 29.6.1 available |
| LLM keys | NONE found (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `OPENROUTER_API_KEY`, `HF_TOKEN` all missing) — baseline LLM steps BLOCKED until user provides one |
| `third_party/` clones (depth-1) | SWE-bench OK, Agentless OK, RepoGraph OK, mini-swe-agent OK, moatless-tools OK, dspy OK, aider OK, OpenHands OK |

Scaffold created: `third_party/`, `docs/`, `experiments/baselines/`, `results/phase1/`, `patchforge/` (empty placeholder).

---

## 1. SWE-bench — https://github.com/SWE-bench/SWE-bench

- **Purpose:** Official benchmark + evaluation infrastructure. Single source of truth for instances, base commits, FAIL_TO_PASS / PASS_TO_PASS, Docker envs, grading.
- **Relevant modules:** `swebench/harness/{run_evaluation.py, grading.py, docker_utils.py, utils.py, reporting.py, log_parsers/}`, `swebench/collect/make_lite/`, dataset aliases in `harness/utils.py::load_swebench_dataset`, CLI `swebench eval [lite|verified] --gold`.
- **What we will reuse:** Whole harness AS-IS. Load Lite via `datasets.load_dataset('SWE-bench/SWE-bench_Lite', split='test')` (300 inst). Emit predictions as `[{"instance_id","model_patch","model_name_or_path"}]` JSON/JSONL. Run `python -m swebench.harness.run_evaluation --dataset_name SWE-bench/SWE-bench_Lite --predictions_path <file> --run_id <id> [--instance_ids ...]`. Smoke-test with `--predictions_path gold`.
- **What we will modify:** One documented compat patch: `patches/swebench-windows-newlines.patch` (3 lines: `newline="\n"` on patch.diff + eval.sh writes, UTF-8 on test output). Without it, CRLF breaks `git apply` and bash in-container (proven: gold UNRESOLVED before, RESOLVED after). No-op on Linux. Applied via `git apply`, never committed into `third_party/`. ALWAYS run harness with `PYTHONUTF8=1`.
- **What we will NOT use:** `swebench/collect/*` (dataset construction), `swebench/inference/*` (their patch generation), `swebench/image_builder` custom flows, Modal/cloud eval unless local Docker fails.
- **Integration method:** Black-box subprocess. PatchForge produces `model_patch` (unified diff); SWE-bench evaluates. No import of harness internals except `load_swebench_dataset` helper for metadata.
- **Expected cost/benefit:** Cost $0 (local Docker) + grading time. Benefit: trustworthy official scoring; eliminates ALL custom evaluator work. Risk: Docker image pulls are heavy (disk/time); mitigate with `--instance_ids` single-task runs + `--max_workers 1-4`.

## 2. Agentless — https://github.com/OpenAutoCoder/Agentless

- **Purpose:** Strong non-agentic baseline: hierarchical localization → multi-sample repair → regression/reproduction-test rerank. Reported ~27% Lite at ~$0.34/issue.
- **Relevant modules:** `agentless/fl/{FL.py, localize.py, retrieve.py, Index.py, combine.py}`, `agentless/repair/{repair.py, rerank.py}`, `agentless/test/{run_regression_tests.py, generate_reproduction_tests.py, run_reproduction_tests.py, select_regression_tests.py}`, `agentless/util/{model.py, api_requests.py, preprocess_data.py, postprocess_data.py, compress_file.py}`, `get_repo_structure/*`, entry CLIs `fl/localize.py`, `repair/repair.py`.
- **What we will reuse:** (a) Full pipeline AS BASELINE-1 via its CLIs (unmodified). (b) Smallest components via adapter imports: `compress_file.get_skeleton`, `preprocess_data.{show_project_structure, correct_file_paths, line_wrap_content, transfer_arb_locs_to_locs}`, `postprocess_data.{extract_code_blocks, parse_*_edit_commands, fake_git_repo, normalize_patch}`, `repair.construct_topn_file_context`, `rerank` majority-vote logic, `util/model.py + api_requests.py` LLM facade pattern (not its hardcoded `gpt-4o-2024-05-13` default).
- **What we will modify:** NOTHING in `third_party/Agentless`. All gating (top-k, sampling counts, temperature) overridden from PatchForge adapter args. Model backend switched via `ModelProvider` (see §9), e.g. `gpt-4o-mini` / `llama-3.1-8b-instruct`, NOT Agentless default.
- **What we will NOT use:** Blind copy of the whole 40-sample + 40-test validation loop (too expensive for PatchForge default); Agentless's embedded `swebench` pinned dependency; its reproduction-test voting as mandatory path (only in BASELINE-1 comparison).
- **Integration method:** (i) Baseline mode: subprocess call to upstream scripts. (ii) PatchForge mode: `patchforge/integrations/agentless.py` adapter — Python import of the pure functions above, wrapped to `localize(problem, repo) -> ranked candidates` with Evidence attached. No fork.
- **Expected cost/benefit:** Reuse saves building localization/repair prompts + diff parsing. Cost per task stays ~$0.10–0.35 for baseline; PatchForge path uses fewer samples (bounded §11) so cheaper. Risk: Agentless needs `OPENAI_API_KEY` + Python 3.11 (we have 3.12 — verify; use venv/conda if needed).

## 3. RepoGraph — https://github.com/ozyyshr/RepoGraph

- **Purpose:** Plug-in repo-level code graph (`def`/`ref` edges) for targeted structural context. Boosts both procedural (Agentless) and agentic pipelines.
- **Relevant modules:** `repograph/{construct_graph.py (CodeGraph), graph_searcher.py (RepoSearcher), utils.py (create_structure, parse_python_file)}`, `run_repograph_agentless.sh`, `run_repograph_sweagent.sh`, cached graphs (`repo_structures/` via HF `MrZilinXiao/RepoGraph` / GDrive).
- **What we will reuse:** `CodeGraph.get_code_graph / tag_to_graph`, `RepoSearcher.{one_hop, two_hop, bfs, dfs}`, `utils.{create_structure, parse_python_file}`, cached `.pkl` graphs when available. `--repo_graph` gating pattern from its Agentless fork as reference.
- **What we will modify:** Nothing upstream EXCEPT four documented compat/robustness patches in `patches/` (all `git apply`, never committed): `repograph-windows-paths.patch` (POSIX rel_fname), `repograph-missing-tag-guard.patch` (skip tags from unparseable py2 vendored files), `repograph-toplevel-lookup.patch` (top-level files live under basename key), `repograph-tag-dict-access.patch` (`_asdict` in tag_to_graph — broken on all OSes). Verified: 674 nodes / 4692 edges on psf/requests @ a0df2cb. Adapter `patchforge/integrations/repograph.py`: build-once per `base_commit` → cache under `experiments/baselines/graphs/<instance>.pkl` → query per localized symbol → return `GRAPH_RELATION / IMPORT_RELATION / CALL_RELATION` Evidence objects.
- **What we will NOT use:** Its forked copies of `agentless/` + `SWE-agent/` (we use real upstreams); NO new graph implementation in Phase 1; no full-repo graph prompts (targeted 1–2 hop expansion only, per low-cost policy).
- **Integration method:** Offline graph build (`python repograph/construct_graph.py <repo>`), then in-process `RepoSearcher` queries inside PatchForge `Localizer` enrichment step. Do NOT shell out per query.
- **Expected cost/benefit:** Graph build is one-time CPU (~minutes, Python-only via `ast`/`tree-sitter`), queries are free (no LLM). Benefit: structural evidence for hypothesis ranking at ~zero marginal cost. Risk: Python-only parsing; large repos slow — mitigate with cache + timeout.

## 4. mini-SWE-agent — https://github.com/SWE-agent/mini-swe-agent

- **Purpose:** Minimal (~100-line) bash-only coding-agent execution loop. Reference >74% Verified. Our execution layer — INSTEAD of any custom sandbox.
- **Relevant modules:** `src/minisweagent/agents/default.py (DefaultAgent, AgentConfig)`, `models/litellm_model.py (LitellmModel)`, `environments/{local.py, docker.py} (LocalEnvironment.execute)`, `run/{mini.py, hello_world.py, benchmarks/swebench*.py}`, `config/*.yaml` templates, Protocols in `__init__.py`.
- **What we will reuse:** `LocalEnvironment` (or `DockerEnvironment` when isolation needed) for ALL shell/test execution; `LitellmModel` as one `ModelProvider` backend (gives any-model support incl. `openai/gpt-4o-mini`, `meta-llama/llama-3.1-8b-instruct` via LiteLLM/OpenRouter); `DefaultAgent` loop + YAML templates as reference for bounded loop; `run/benchmarks/swebench.py` as baseline runner reference.
- **What we will modify:** NOTHING upstream. Adapter `patchforge/integrations/minisweagent.py`: thin wrapper exposing `inspect / modify / run_tests / collect_output / revert` using the environment class; reasoning stays in PatchForge orchestrator (keep execution separate from reasoning per spec §11).
- **What we will NOT use:** Interactive agents (`interactive.py`), Modal/contree/singularity backends in Phase 1, full autonomous long-horizon runs (we bound to 3 patch attempts / 1 retry).
- **Integration method:** Import `LocalEnvironment` + `LitellmModel` as libraries; PatchForge `repair/loop.py` drives them. Baseline mode: `mini -t <task> -m <model>` subprocess for comparison.
- **Expected cost/benefit:** Zero sandbox engineering; battle-tested exec + model routing. Cost = model tokens only. Risk: needs LLM key + `litellm` install; mitigate with venv + `.env`.

## 5. OpenHands — https://github.com/All-Hands-AI/OpenHands

- **Purpose:** REFERENCE ONLY for agent/runtime/environment architecture.
- **Relevant modules:** `openhands/runtime/*`, `openhands/agent/*`, Docker runtime docs (survey only).
- **What we will reuse:** Design ideas (runtime separation, event sourcing) if mini-SWE-agent env proves insufficient. NOTHING imported in Phase 1.
- **What we will modify:** Nothing.
- **What we will NOT use:** OpenHands as a dependency (too heavy: Node+Python monorepo, Docker-heavy). Explicitly NOT mandatory per spec.
- **Integration method:** Docs study only.
- **Expected cost/benefit:** Benefit: avoids dead-end custom runtime designs. Cost: ~0 (no install needed beyond clone).

## 6. Moatless Tools — https://github.com/yibaoable/moatless-tools

- **Purpose:** REFERENCE for repo navigation/context + iterative repair patterns.
- **Relevant modules:** `moatless/` (survey: context/retrieval helpers) — adapt snippets only if a concrete gap appears.
- **What we will reuse:** TBD small helpers (to be named if/when needed).
- **What we will modify:** Nothing upstream; copy-and-attribute minimal snippet into adapter if justified.
- **What we will NOT use:** Entire framework import.
- **Integration method:** Reference/adapt, not depend.
- **Expected cost/benefit:** Low cost, avoids framework lock-in.

## 7. Aider — https://github.com/Aider-AI/aider

- **Purpose:** REFERENCE for repo-map, code editing, git-aware diffs, test-driven interaction.
- **Relevant modules:** `aider/repomap.py` (already the ancestor of RepoGraph's graph builder — so covered via RepoGraph), edit/diff helpers (survey).
- **What we will reuse:** Only if RepoGraph/mini-SWE-agent editing proves insufficient; then a narrow edit helper.
- **What we will modify:** Nothing upstream.
- **What we will NOT use:** Aider as a mandatory dependency / chat loop.
- **Integration method:** Reference only in Phase 1.
- **Expected cost/benefit:** Saves building repo-map/edit from scratch; cost ~0.

## 8. DSPy — https://github.com/stanfordnlp/dspy

- **Purpose:** FUTURE prompt/program optimization (Phase 3+).
- **Relevant modules:** None in Phase 1.
- **What we will reuse:** NOTHING yet. Design PatchForge interfaces (prompts as modules, metrics as functions) so DSPy optimizers can wrap them later.
- **What we will modify:** Nothing.
- **What we will NOT use:** Any adaptive optimization, RL, prompt bandit in Phase 1.
- **Integration method:** Interface hygiene only (keep prompts/config centralised in `patchforge/reasoning/`, `patchforge/config.py`).
- **Expected cost/benefit:** Zero Phase-1 cost; preserves Phase-3 optionality.

---

## 9. Integration matrix (who calls whom)

| PatchForge layer | Upstream used | Adapter | Mode |
|---|---|---|---|
| `integrations/swebench.py` | SWE-bench harness | subprocess + predictions JSON | eval only |
| `integrations/agentless.py` | Agentless `fl/` + `repair/` pure fns | import + wrap → `Candidate(evidence[])` | baseline-1 subprocess; PatchForge import |
| `integrations/repograph.py` | RepoGraph `CodeGraph` + `RepoSearcher` | build-once/cache, query per symbol | enrichment (BASELINE-2 on/off) |
| `integrations/minisweagent.py` | mini-SWE-agent env + model | import `LocalEnvironment`, `LitellmModel` | execution for all modes |
| `models/provider.py`, `models/router.py` | LiteLLM (via mini) | `ModelProvider` abstraction | `gpt-4o-mini` / `llama-3.1-8b-instruct` interchangeable |
| OpenHands / Moatless / Aider / DSPy | none imported | — | reference / future |

**Forbidden:** editing `third_party/*` directly. If a fix is unavoidable: document why, wrap in adapter, keep as patch file. Reproducibility first.

---

## 10. Baselines & PatchForge experiment modes (spec §20)

- **BASELINE-1 (Agentless-style):** issue → localization → repair → eval.
- **BASELINE-2 (Agentless + RepoGraph):** issue → localization + graph context → repair → eval.
- **PATCHFORGE:** issue → interpretation → Agentless localization + RepoGraph evidence → evidence aggregation → 2–3 hypotheses → deterministic ranking → hypothesis-tied minimal patch → mini-SWE-agent exec → targeted→broad→official tests → bounded retry (3 hypotheses / 3 patches / 1 major retry) → classified failure.
- Metrics per spec §21 + `results/phase1/<instance>.json` schema per spec §19. Failure classes per spec §12.

## 11. Cost policy (spec §15)

Retrieve-before-reason; targeted context only; 2–3 hypotheses; limited candidates; targeted-tests-first; early stop on strong evidence; cache graphs/embeddings; record tokens/cost/runtime per episode in `memory/episode.py`. Optimize `repairs / $`, not raw count.

## 12. Phase-1 cohort + integrity notes

- Cohort: 16 Lite instances — Django 4, Flask 3, Pytest 3, Requests 3, SymPy 3. **Exact 16 IDs NOT fully specified in the brief** — only Requests pool given (`psf__requests-1963, -2148, -2674, -3362, -2317, -863`, pick 3 in agreed cohort) + explicit non-ID `psf__requests-2679 DOES NOT EXIST` (never synthesize/substitute). **Action:** confirm the 12 remaining IDs with requester before Phase D; default to first-N Lite IDs per repo if unanswered, recorded in `experiments/baselines/cohort.json`.
- Integrity: every task from `base_commit` (clone → checkout → verify clean) → PatchForge → candidate patch → official eval. NEVER use gold/reference patch or patched source; only problem + repo at base commit.

## 13. Phase B baseline results (DONE 2026-09-06, psf__requests-863)

1. `experiments/baselines/00_swebench_gold_smoke/psf__requests-863.json` — gold RESOLVED (F2P 4/4, P2P 63/63). Eval trustworthy.
2. `experiments/baselines/01_agentless/psf__requests-863.json` — full Agentless loop via OpenRouter (`gpt-4o-mini-2024-07-18`); patch applied, UNRESOLVED/WRONG_HYPOTHESIS; $0.052 / 9210 tokens.
3. `experiments/baselines/02_repograph/psf__requests-863.json` — graph 674n/4692e, queries resolve, zero LLM cost.
4. `experiments/baselines/03_minisweagent/mini_smoke_record.json` — agent loop Submitted correct answer in 6 msgs.
Details + provider constraints: `experiments/baselines/README.md`. Cohort locked APPROVED (`cohort.json`).
Next: Phase C `patchforge/` package (spec §4), starting from these proven adapters.

## 14. What we will NOT build (Phase 1)

Custom SWE-bench evaluator, Docker infra, agent runtime, repo graph, embedding DB (unless required), terminal env, RL / bandit / temporal graph / debate / self-learning / big memory / web search. (Spec §25.)

---
*Next: Phase B baselines → then Phase C `patchforge/` skeleton per spec §4 (orchestrator, state, config, issue, retrieval, localization, reasoning, repair, verification, memory, models, integrations, telemetry).*
