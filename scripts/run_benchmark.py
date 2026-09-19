"""Benchmark runner for PatchForge AI evaluating instance cohorts with structured telemetry.

Usage:
    python scripts/run_benchmark.py --cohort smoke3 --mode baseline --model qwen2.5-coder:7b
    python scripts/run_benchmark.py --instances psf__requests-863,psf__requests-2674 --mode baseline
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
load_dotenv()

sys.path.insert(0, ".")

from patchforge.core.config import PatchForgeConfig
from patchforge.integrations.swebench import SWEBenchAdapter
from patchforge.models.provider import OllamaProvider
from patchforge.pipeline.baseline import BaselineRepairEngine
from patchforge.verification.classifier import FailureClass
from patchforge.verification.tester import Tester

COHORTS = {
    "smoke3": [
        "psf__requests-863",
        "psf__requests-2674",
        "pallets__flask-4992",
    ],
    "smoke4": [
        "psf__requests-863",
        "psf__requests-2674",
        "pallets__flask-4992",
        "django__django-11099",
    ],
}


def run_cohort(instances: list[str], mode: str, model: str, workspace: str, output_dir: str):
    cfg = PatchForgeConfig(default_model=model, workspace=workspace)
    swebench = SWEBenchAdapter(cfg)
    tester = Tester(cfg, swebench)
    provider = OllamaProvider(model=model)

    results: list[dict[str, Any]] = []
    t_start = time.time()

    print(f"\n============================================================")
    print(f"PATCHFORGE BENCHMARK EVALUATION")
    print(f"Instances: {len(instances)} | Mode: {mode} | Model: {model}")
    print(f"============================================================\n")

    for idx, inst_id in enumerate(instances, 1):
        print(f"[{idx}/{len(instances)}] Running {inst_id} ...", flush=True)
        t0 = time.time()
        try:
            problem = swebench.load_problem(inst_id)
            repos_dir = str(Path(workspace) / "experiments" / "phase_d_v03" / "repos")
            repo_dir = swebench.ensure_checkout(problem, repos_dir)

            engine = BaselineRepairEngine(provider=provider, model_name=model, workspace=workspace)
            res = engine.run(problem=problem, repo_dir=repo_dir, tester=tester)

            record = {
                "instance_id": inst_id,
                "status": "COMPLETED",
                "target_file": res.target.file_path if res.target else "",
                "target_symbol": res.target.symbol if res.target else "",
                "patch_applied": res.patch_applied,
                "patch_valid": res.patch.valid if res.patch else False,
                "test_executed": res.test_executed,
                "resolved": res.resolved,
                "failure_class": res.failure_class,
                "runtime_s": res.runtime_s,
                "telemetry": res.telemetry,
                "diff": res.patch.patch_text if res.patch else "",
            }
        except Exception as e:
            record = {
                "instance_id": inst_id,
                "status": "ERROR",
                "error": str(e),
                "patch_applied": False,
                "patch_valid": False,
                "test_executed": False,
                "resolved": False,
                "failure_class": FailureClass.INFRA_FAILURE.value,
                "runtime_s": round(time.time() - t0, 2),
                "telemetry": {},
                "diff": "",
            }

        results.append(record)
        print(f"    Target: {record.get('target_file', 'None')} | Patch: {record['patch_applied']} | Valid: {record['patch_valid']} | Class: {record['failure_class']} | Time: {record['runtime_s']}s\n", flush=True)

    total_time = round(time.time() - t_start, 2)
    n = len(results)
    applied_count = sum(1 for r in results if r["patch_applied"])
    valid_count = sum(1 for r in results if r["patch_valid"])
    test_count = sum(1 for r in results if r["test_executed"])
    resolved_count = sum(1 for r in results if r["resolved"])

    total_tokens = sum(r.get("telemetry", {}).get("total_tokens", 0) for r in results)
    llm_times = [r.get("telemetry", {}).get("llm_time_s", 0) for r in results]
    mean_llm_time = round(sum(llm_times) / n, 2) if n else 0.0

    failure_breakdown = {}
    for r in results:
        fc = r.get("failure_class", "UNKNOWN")
        failure_breakdown[fc] = failure_breakdown.get(fc, 0) + 1

    summary = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "model": model,
        "mode": mode,
        "total_instances": n,
        "metrics": {
            "patch_applied_rate": round(applied_count / n, 4) if n else 0.0,
            "patch_valid_rate": round(valid_count / n, 4) if n else 0.0,
            "test_executed_rate": round(test_count / n, 4) if n else 0.0,
            "resolution_rate": round(resolved_count / n, 4) if n else 0.0,
            "mean_runtime_s": round(total_time / n, 2) if n else 0.0,
            "mean_llm_time_s": mean_llm_time,
            "total_tokens": total_tokens,
            "mean_tokens_per_task": round(total_tokens / n, 1) if n else 0.0,
        },
        "failure_breakdown": failure_breakdown,
        "instances": results,
    }

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    report_file = out_dir / f"benchmark_{int(time.time())}.json"
    report_file.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print("=" * 60)
    print("BENCHMARK AGGREGATE SUMMARY:")
    print(f"Total Tasks:        {n}")
    print(f"Patch Applied Rate: {applied_count}/{n} ({summary['metrics']['patch_applied_rate']*100:.1f}%)")
    print(f"Patch Valid Rate:   {valid_count}/{n} ({summary['metrics']['patch_valid_rate']*100:.1f}%)")
    print(f"Test Executed Rate: {test_count}/{n} ({summary['metrics']['test_executed_rate']*100:.1f}%)")
    print(f"Resolution Rate:    {resolved_count}/{n} ({summary['metrics']['resolution_rate']*100:.1f}%)")
    print(f"Mean Runtime:       {summary['metrics']['mean_runtime_s']}s")
    print(f"Mean LLM Time:      {summary['metrics']['mean_llm_time_s']}s")
    print(f"Mean Tokens/Task:   {summary['metrics']['mean_tokens_per_task']}")
    print(f"Failures:           {failure_breakdown}")
    print(f"Full report saved:  {report_file}")
    print("=" * 60)
    return summary


def main():
    parser = argparse.ArgumentParser(description="PatchForge AI Benchmark Cohort Runner")
    parser.add_argument("--cohort", type=str, default="", help="Predefined cohort name (smoke3, smoke4)")
    parser.add_argument("--instances", type=str, default="", help="Comma-separated instance IDs")
    parser.add_argument("--mode", type=str, default="baseline", choices=["baseline", "agent"], help="Execution mode")
    parser.add_argument("--model", type=str, default="qwen2.5-coder:7b", help="Model identifier")
    parser.add_argument("--workspace", type=str, default=".", help="Workspace path")
    parser.add_argument("--output-dir", type=str, default="experiments/benchmark_reports", help="Output directory")
    args = parser.parse_args()

    if args.cohort:
        if args.cohort not in COHORTS:
            print(f"Error: Unknown cohort '{args.cohort}'. Available: {list(COHORTS.keys())}")
            sys.exit(1)
        instance_list = COHORTS[args.cohort]
    elif args.instances:
        instance_list = [i.strip() for i in args.instances.split(",") if i.strip()]
    else:
        instance_list = COHORTS["smoke3"]

    run_cohort(instance_list, args.mode, args.model, args.workspace, args.output_dir)


if __name__ == "__main__":
    main()
