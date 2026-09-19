"""Targeted single-task real-model validation on psf__requests-863."""
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

sys.path.insert(0, ".")

from patchforge.core.config import PatchForgeConfig
from patchforge.core.orchestrator_v03 import OrchestratorV03


def main():
    instance_id = "psf__requests-863"
    model = "qwen2.5-coder:7b"
    print(f"=== Starting Real-Model Validation on {instance_id} with {model} ===", flush=True)

    t0 = time.time()
    cfg = PatchForgeConfig(
        default_model=model,
        max_patch_attempts=4,
        max_task_seconds=1500,
        workspace=".",
    )

    orch = OrchestratorV03(config=cfg, workspace=".")
    best_patch, trajectory, state = orch.run_instance(instance_id=instance_id, max_turns=10)
    elapsed = round(time.time() - t0, 1)

    print("\n" + "=" * 60, flush=True)
    print(f"VALIDATION FINISHED in {elapsed}s", flush=True)
    print(f"Turns Used: {state.turn_count}", flush=True)
    print(f"Final Phase: {state.phase.value}", flush=True)
    print(f"Patch Applied: {bool(state.best_patch or state.active_patch)}", flush=True)
    if state.best_patch:
        print(f"Patch Files: {state.best_patch.files_changed}", flush=True)
        print(f"Patch Match Tier: {state.best_patch.match_tier}", flush=True)
    print(f"Execution Evidence: {bool(state.last_execution_evidence)}", flush=True)
    if state.last_execution_evidence:
        ee = state.last_execution_evidence
        print(f"Exit Code: {ee.exit_code}", flush=True)
        print(f"F2P: {ee.fail_to_pass_passed}/{ee.fail_to_pass_total}", flush=True)
        print(f"P2P Regressions: {ee.pass_to_pass_regressions}", flush=True)
    print(f"Resolved: {trajectory.resolved}", flush=True)
    print("=" * 60, flush=True)


if __name__ == "__main__":
    main()
