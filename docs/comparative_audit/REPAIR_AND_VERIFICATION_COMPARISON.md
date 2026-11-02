# Repair and Verification Comparison

## Editing

PatchForge converts structured action JSON into a planned AST/line span and mutates source via `SourceReconstructor`. This constrains edits to a target and validates Python syntax, but it risks coordinate/indentation mismatch and infers imports. It has multi-edit overlap detection but does not demonstrate dependable coordinated multi-file repair in the historical result.

Agentless `repair.py` exposes either line `edit_file` commands, SEARCH/REPLACE, or string-replace parsing, builds bounded context intervals, parses multi-file command groups and creates unified diffs. Aider’s `EditBlockCoder.apply_edits` applies contiguous SEARCH/REPLACE blocks, tries permitted chat files if the named file misses, returns detailed failed-match observations with similar lines, and detects no-op replacements. Neither mechanism is inherently superior: all rely on model-provided text. Their useful feature is recoverable, source-grounded mismatch feedback.

## Verification

| Capability | PatchForge | Source-observed reference mechanism | Proposed adaptation |
|---|---|---|---|
| Fast edit applicability | AST/static validation | Aider exact/fuzzy application and diagnostic result | Keep static validation; add exact source-match diagnostics. |
| Candidate diversity | retry overwrites candidate | Agentless supports multiple generation/validation artifacts | Generate N bounded candidates only when deterministic checks pass; retain all digests. |
| Test interaction | official harness after complete patch | SWE-agent exposes command execution in agent loop | Provide only safe `run_targeted_test`, `read_file`, `search`, `git_diff`; enforce budget. |
| Regression selection | report P2P outcomes | Agentless says it uses validation/reranking; SWE-agent retry reviewer selects attempt | deterministic hard reject of regressions, then configurable ranking. |
| Failure recovery | generic replanner action | Aider/SWE-agent make parser/command error the next observation | typed failure observation with exact test/edit phase. |

## Tests required

Test uniqueness/ambiguity/no-op/missing-file edits; comments/decorators/CRLF/multifile preservation; targeted-test timeout/output parsing; candidate rollback after P2P failure; resume after worker failure; and “same model response, same applied patch” reproducibility. Do not claim CST is required until these characterize concrete current reconstruction breakages.
