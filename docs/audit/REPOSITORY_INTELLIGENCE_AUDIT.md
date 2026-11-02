# Repository Intelligence Audit

## What exists

`RepositoryIndexer` parses Python files in a thread pool, caches a `RepositoryGraph` and `TestGraphIndex`; `RepositoryGraph` stores AST-derived symbols/edges in NetworkX. `TemporalRepositoryGraph` reads bounded history. `HybridRepositoryRetriever` describes lexical, dense, graph, test and temporal signals.

## What the canonical retriever actually does

`patchforge/retrieval/hybrid.py` has Okapi BM25 code (line 73), computes a token n-gram similarity in `_compute_dense_score` (118), and linearly weights signals (around 235). This is not demonstrated semantic embedding retrieval, reciprocal-rank fusion, or reranking. Documentation should call it heuristic hybrid scoring until those features exist.

## Assessment

| Signal | Status | Value evidence | Decision |
|---|---|---|---|
| AST symbols/call/import hierarchy | Implemented | deterministic context; no localization-recall metric | Keep, instrument. |
| Test assertion index | Implemented | no mapping from observed harness failure to candidate rank in V1.9 | Keep, connect to test output. |
| Temporal churn | Implemented | hand-tuned influence, no ablation | Gate behind experiment. |
| “Dense” score | Token overlap, not embeddings | no proof it improves rank | Rename or replace only after baseline. |
| Graph traversals | Implemented | symbol resolution can be ambiguous; `related_symbols(max_depth)` does not use `max_depth` | Fix/measure before expansion. |
| Dynamic traces/probes | Available but disabled by default | no V1.9 contribution | Experimental only. |

## Immediate measurement plan

For each task, log candidate top-1/top-3 file and symbol against a manually derived issue-to-patch target only in a sealed evaluation analysis—not in repair prompts. Log test-to-symbol retrieval coverage, graph expansion size/latency, and final selected target. Run ablations: BM25 alone; BM25+test; +graph; +temporal. Promote only a signal with predeclared improvement in localization and no unacceptable latency cost.

## Do not add yet

Do not add slicing, mutation testing, a vector database, or temporal graph complexity merely because they are available. Add backward slicing after failure traces reliably identify a statement/symbol; add coverage tracing only when test selection dominates runtime; add embeddings only if BM25/test/graph ablation leaves a measured semantic recall gap.
