"""Phase D v0.3 Batch Runner: PatchForge v0.3 Autonomous Debugging Agent.

Writes results to results/phase_d_v03/C_patchforge/<id>.json.
Phase D (v0.1) and Phase D (v0.2) results in results/phase_d/ and results/phase_d_v02/ remain immutable.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

sys.path.insert(0, ".")

from patchforge.core.config import PatchForgeConfig
from patchforge.core.orchestrator_v03 import OrchestratorV03


def record_path(instance_id: str) -> str:
    return os.path.join("results", "phase_d_v03", "C_patchforge", instance_id + ".json")


def save_record(instance_id: str, rec: dict) -> None:
    p = record_path(instance_id)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(rec, f, indent=2)


def run_v03_instance(instance_id: str, model: str = "") -> dict:
    t0 = time.time()
    chosen_model = model or os.getenv("PATCHFORGE_MODEL", "qwen3:8b")
    cfg = PatchForgeConfig(
        default_model=chosen_model,
        max_patch_attempts=4,
        max_task_seconds=1500,
        workspace=".",
    )

    orch = OrchestratorV03(config=cfg, workspace=".")
    best_patch, trajectory, state = orch.run_instance(instance_id=instance_id, max_turns=12)


    ee = state.last_execution_evidence
    patch_calls_count = sum(
        1
        for s in trajectory.steps
        if getattr(s, "action_name", "") == "apply_patch"
        and getattr(s, "tool_result", {}).get("status") != "BLOCKED"
    )
    cf_verdict = (
        "CONTROL_FLOW_RESOLVED_AND_PATCH_SUCCEEDED" if (state.best_score >= 1.0 or trajectory.resolved)
        else ("CONTROL_FLOW_RESOLVED_AND_PATCH_ATTEMPTED" if (state.best_patch or len(state.patch_attempts) > 0 or patch_calls_count > 0)
              else "CONTROL_FLOW_FAILED")
    )

    rec = {
        "version": "v0.3",
        "instance": instance_id,
        "status": "RESOLVED" if (state.best_score >= 1.0 or trajectory.resolved) else ("UNRESOLVED" if state.best_patch else "INCOMPLETE"),
        "control_flow_verdict": cf_verdict,
        "patch_calls_count": patch_calls_count,
        "localization_transition_trigger": state.localization_transition_trigger,
        "guard_firings": dict(state.guard_firings),
        "turns_used": state.turn_count,
        "total_trajectory_steps": len(trajectory.steps),
        "best_score": state.best_score,
        "best_patch_valid": bool(state.best_patch and state.best_patch.valid),
        "best_patch_tier": state.best_patch.match_tier if state.best_patch else None,
        "visited_files_count": len(state.visited_files),
        "evidence_count": len(state.evidence_store),
        "distinct_evidence_count": state.distinct_evidence_count(),
        "FAIL_TO_PASS": {
            "passed": ee.fail_to_pass_passed if ee else 0,
            "total": ee.fail_to_pass_total if ee else 0,
        },
        "PASS_TO_PASS_regressions": ee.pass_to_pass_regressions if ee else 0,
        "input_tokens": state.input_tokens,
        "output_tokens": state.output_tokens,
        "total_tokens": state.input_tokens + state.output_tokens,
        "cost_usd": state.total_cost_usd,
        "runtime_s": round(time.time() - t0, 1),
    }

    save_record(instance_id, rec)
    return rec


def main():
    parser = argparse.ArgumentParser(description="Run PatchForge v0.3 on tasks.")
    parser.add_argument("--instance", type=str, help="Specific instance_id to run (e.g. psf__requests-863)")
    parser.add_argument("--smoke", action="store_true", help="Run the 4 regression smoke tasks")
    parser.add_argument("--model", type=str, default="", help="Model name (e.g. qwen3:8b, gemma3:12b)")
    args = parser.parse_args()

    smoke_instances = [
        "psf__requests-863",
        "psf__requests-2674",
        "psf__requests-1963",
        "pallets__flask-4045",
    ]

    if args.instance:
        run_v03_instance(args.instance, model=args.model)
    elif args.smoke:
        print(f"Starting smoke regression run on {len(smoke_instances)} tasks: {smoke_instances}")
        for inst in smoke_instances:
            run_v03_instance(inst, model=args.model)
    else:
        print("Please provide --instance <id> or --smoke")



if __name__ == "__main__":
    main()
