# PatchForge v0.3 — Coding Model (qwen2.5-coder:7b) Smoke Benchmark Report

## 1. Executive Summary

We evaluated PatchForge v0.3 on the frozen 4-task SWE-bench benchmark (`psf__requests-863`, `psf__requests-2674`, `psf__requests-1963`, `pallets__flask-4045`) using the coding-specialized model `qwen2.5-coder:7b`.

Compared to general reasoning models (`qwen3:8b`), `qwen2.5-coder:7b` eliminated reasoning monologue timeouts and demonstrated rapid, structured tool invocation (average turn latency ~15–20s vs. >300s timeout). Across all 4 instances, the system executed full 12-turn trajectories without stalling.

Key Milestones Reached:
- **4/4** tasks completed all 12 turns with active tool invocation
- **4/4** tasks reached HYPOTHESIZE and generated hypotheses
- **4/4** tasks reached PLAN
- **2/4** tasks reached PATCH phase
- **1/4** tasks (`psf__requests-1963`) repeatedly emitted `apply_patch` actions
- **0/4** unverified patches were allowed through to the repo (target context verification guard successfully blocked premature hallucinated edits)
- **0/4** resolved (due to verification-before-patch sequencing loop on `psf__requests-1963` and localization search wandering on others)

---

## 2. Cohort Experimental Results

| Instance ID | Turns | Phases Reached | Tool Calls Emitted | Guard Interventions | Outcome | Runtime (s) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| `psf__requests-863` | 12 | INVESTIGATE, HYPOTHESIZE, PLAN | 12 (`find_symbol`, `search_code`, `read_file`) | `exact_rep: 2`, `zero_info: 1`, `invalid_path: 2`, `circuit_breaker: 1` | INCOMPLETE | 206.1 |
| `psf__requests-2674` | 12 | INVESTIGATE, HYPOTHESIZE, PLAN | 12 (`search_code`, `formulate_hypothesis`, `read_file`, `find_symbol`) | `confidence_trans: 1`, `invalid_path: 1` | INCOMPLETE | 194.0 |
| `psf__requests-1963` | 12 | INVESTIGATE, HYPOTHESIZE, PLAN, PATCH | 12 (`formulate_hypothesis`, `propose_plan`, `apply_patch` x10) | `unverified_patch: 4`, `exact_rep: 6` | INCOMPLETE | 181.5 |
| `pallets__flask-4045` | 12 | INVESTIGATE, HYPOTHESIZE, PLAN, PATCH | 12 (`find_symbol`, `find_references`, `search_code`, `read_file`, passive) | `zero_info: 1`, `circuit_breaker: 1`, `passive_recovery: 5` | INCOMPLETE | 411.2 |

---

## 3. Diagnostic Analysis & Findings

1. **Model Efficiency**: `qwen2.5-coder:7b` executed turns in 15–30s on 100% GPU offload, completely fixing the timeout failure mode seen with general reasoning models.
2. **Context Verification Guard Functionality**: In `psf__requests-1963`, the model attempted `apply_patch` directly at Turn 3 without first reading the source file. The `unverified_patch_blocks` guard correctly prevented an unverified hallucinated patch from being applied, enforcing our strict safety policy $\text{PLAN} \to \text{VERIFY CONTEXT} \to \text{PATCH}$.
3. **Loop Recovery**: When blocked by `unverified_patch_blocks` and instructed to read the target file, `qwen2.5-coder:7b` persistently repeated the same `apply_patch` action rather than switching to `read_file`.

---

## 4. Next Step Recommendation
To bridge the remaining gap from $\text{PLAN} \to \text{PATCH}$, the controller should enforce an automatic context-fetch / forced `read_file` transition when entering PATCH if the target file has not yet been read, directly providing verified source lines to the model before requesting the patch.
