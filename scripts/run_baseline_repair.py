"""Script to execute deterministic baseline repair on a SWE-bench task."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

sys.path.insert(0, ".")

from patchforge.core.config import PatchForgeConfig
from patchforge.integrations.swebench import SWEBenchAdapter
from patchforge.models.provider import OllamaProvider
from patchforge.pipeline.baseline import BaselineRepairEngine
from patchforge.verification.tester import Tester


def main():
    parser = argparse.ArgumentParser(description="Run PatchForge Deterministic Baseline Repair")
    parser.add_argument("--instance", type=str, default="psf__requests-863", help="SWE-bench instance ID")
    parser.add_argument("--model", type=str, default="qwen2.5-coder:7b", help="Model name")
    parser.add_argument("--workspace", type=str, default=".", help="Workspace directory")
    args = parser.parse_args()

    cfg = PatchForgeConfig(default_model=args.model, workspace=args.workspace)
    swebench = SWEBenchAdapter(cfg)
    tester = Tester(cfg, swebench)

    print(f"=== PatchForge Baseline Repair: {args.instance} ({args.model}) ===", flush=True)
    t0 = time.time()

    # 1. Load Problem & Checkout
    problem = swebench.load_problem(args.instance)
    repos_dir = str(Path(args.workspace) / "experiments" / "phase_d_v03" / "repos")
    repo_dir = swebench.ensure_checkout(problem, repos_dir)

    # 2. Execute Baseline Engine
    provider = OllamaProvider(model=args.model)
    engine = BaselineRepairEngine(provider=provider, model_name=args.model, workspace=args.workspace)
    result = engine.run(problem=problem, repo_dir=repo_dir, tester=tester)
    elapsed = round(time.time() - t0, 2)

    # 3. Print Results & Telemetry
    print("\n" + "=" * 60, flush=True)
    print(f"BASELINE REPAIR COMPLETE in {elapsed}s", flush=True)
    print(f"Instance ID:    {result.instance_id}", flush=True)
    if result.target:
        print(f"Repair Target:  {result.target.file_path} (Symbol: {result.target.symbol}, Lines: {result.target.line_start}-{result.target.line_end})", flush=True)
    print(f"Patch Applied:  {result.patch_applied}", flush=True)
    if result.patch:
        print(f"Patch Valid:    {result.patch.valid} (Tier: {result.patch.match_tier})", flush=True)
        print(f"Patch Text:\n{result.patch.patch_text[:500]}...", flush=True)
    print(f"Test Executed:  {result.test_executed}", flush=True)
    print(f"Resolved:       {result.resolved}", flush=True)
    print(f"Failure Class:  {result.failure_class}", flush=True)
    print(f"\nTelemetry Breakdown:\n{json.dumps(result.telemetry, indent=2)}", flush=True)
    print("=" * 60, flush=True)


if __name__ == "__main__":
    main()
