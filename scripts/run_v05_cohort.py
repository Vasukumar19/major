"""20-Task Cohort Runner for PatchForge V0.5 Baseline & Diagnostics.

Usage:
    python scripts/run_v05_cohort.py --mode baseline --model qwen2.5-coder:7b
    python scripts/run_v05_cohort.py --instances psf__requests-863,pallets__flask-4045 --force
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

docker_bin = r"C:\Program Files\Docker\Docker\resources\bin"
if os.path.exists(docker_bin) and docker_bin not in os.environ.get("PATH", ""):
    os.environ["PATH"] = docker_bin + os.pathsep + os.environ.get("PATH", "")

sys.path.insert(0, ".")

from patchforge.core.config import PatchForgeConfig
from patchforge.integrations.swebench import SWEBenchAdapter
from patchforge.models.provider import OllamaProvider
from patchforge.pipeline.baseline import BaselineRepairEngine
from patchforge.pipeline.graph_engine import GraphRepairEngine
from patchforge.verification.classifier import FailureClass
from patchforge.verification.tester import Tester


V05_COHORT_20 = [
    # Golden Regression Invariants (Requests & Flask)
    "psf__requests-863",
    "pallets__flask-4045",
    # Additional PSF Requests cohort
    "psf__requests-1963",
    "psf__requests-2674",
    "psf__requests-2148",
    "psf__requests-2317",
    "psf__requests-3362",
    # Pallets Flask cohort
    "pallets__flask-4992",
    "pallets__flask-5063",
    # Pytest cohort
    "pytest-dev__pytest-5103",
    "pytest-dev__pytest-5221",
    "pytest-dev__pytest-5227",
    "pytest-dev__pytest-5495",
    "pytest-dev__pytest-5692",
    "pytest-dev__pytest-7220",
    "pytest-dev__pytest-7373",
    "pytest-dev__pytest-7490",
    # Pylint cohort
    "pylint-dev__pylint-5859",
    "pylint-dev__pylint-7114",
    "pylint-dev__pylint-7228",
]


def run_cohort(
    instances: list[str],
    model: str = "qwen2.5-coder:14b",
    engine_name: str = "graph",
    workspace: str = ".",
    results_dir: str = "results/v05_graph",
    max_retries: int = 3,
    max_refinements: int = 3,
    force: bool = False,
):
    out_dir = Path(results_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    cfg = PatchForgeConfig(default_model=model, workspace=workspace)
    swebench = SWEBenchAdapter(cfg)
    tester = Tester(cfg, swebench)
    provider = OllamaProvider(model=model)

    records: list[dict[str, Any]] = []
    print("=" * 70, flush=True)
    print(f"PATCHFORGE V0.5 COHORT EVALUATION ({len(instances)} tasks)", flush=True)
    print(f"Engine: {engine_name.upper()} | Model: {model} | Results Dir: {results_dir} | Force: {force}", flush=True)
    print("=" * 70, flush=True)

    t_all_start = time.time()

    for idx, inst_id in enumerate(instances, 1):
        inst_json = out_dir / f"{inst_id}.json"
        if not force and inst_json.exists():
            print(f"[{idx:02d}/{len(instances):02d}] {inst_id} (CACHED)", flush=True)
            try:
                rec = json.loads(inst_json.read_text(encoding="utf-8"))
                records.append(rec)
                print(f"    -> Resolved: {rec.get('resolved')} | F2P: {rec.get('f2p_passed')}/{rec.get('f2p_total')} | P2P: {rec.get('p2p_passed')}/{rec.get('p2p_total')} | Class: {rec.get('failure_class')}", flush=True)
                continue
            except Exception:
                pass

        print(f"\n[{idx:02d}/{len(instances):02d}] RUNNING: {inst_id} ...", flush=True)
        t0 = time.time()
        try:
            problem = swebench.load_problem(inst_id)
            repos_dir = str(Path(workspace) / "experiments" / "phase_d_v03" / "repos")
            repo_dir = swebench.ensure_checkout(problem, repos_dir)

            if engine_name.lower() == "graph":
                engine = GraphRepairEngine(provider=provider, model_name=model, workspace=workspace)
            else:
                engine = BaselineRepairEngine(provider=provider, model_name=model, workspace=workspace)

            res = engine.run(
                problem=problem,
                repo_dir=repo_dir,
                tester=tester,
                max_retries=max_retries,
                max_refinements=max_refinements,
            )


            ts = res.test_summary or {}
            f2p = ts.get("fail_to_pass", {})
            p2p = ts.get("pass_to_pass", {})
            f2p_passed = f2p.get("passed", ts.get("fail_to_pass_passed", 0)) if isinstance(f2p, dict) else ts.get("fail_to_pass_passed", 0)
            f2p_total = f2p.get("total", ts.get("fail_to_pass_total", 0)) if isinstance(f2p, dict) else ts.get("fail_to_pass_total", 0)
            p2p_passed = p2p.get("passed", ts.get("pass_to_pass_passed", 0)) if isinstance(p2p, dict) else ts.get("pass_to_pass_passed", 0)
            p2p_total = p2p.get("total", ts.get("pass_to_pass_total", 0)) if isinstance(p2p, dict) else ts.get("pass_to_pass_total", 0)

            rec = {
                "instance_id": inst_id,
                "repo": problem.repo,
                "status": "COMPLETED",
                "resolved": res.resolved,
                "patch_applied": res.patch_applied,
                "patch_valid": res.patch.valid if res.patch else False,
                "patch_tier": res.patch.match_tier if res.patch else None,
                "patch_text": res.patch.patch_text if res.patch else "",
                "test_executed": res.test_executed,
                "failure_class": res.failure_class,
                "runtime_s": res.runtime_s,
                "f2p_passed": f2p_passed,
                "f2p_total": f2p_total,
                "p2p_passed": p2p_passed,
                "p2p_total": p2p_total,
                "refinement_cycles": res.refinement_cycles,
                "target_file": res.target.file_path if res.target else "",
                "target_symbol": res.target.symbol if res.target else "",
                "diagnosis": res.diagnosis.to_dict() if hasattr(res, "diagnosis") and hasattr(res.diagnosis, "to_dict") else (res.diagnosis.__dict__ if hasattr(res, "diagnosis") and res.diagnosis else {}),
                "refinement_history": res.refinement_history if hasattr(res, "refinement_history") else [],
                "repair_unit": res.repair_unit.to_dict() if hasattr(res, "repair_unit") and res.repair_unit else {},
                "ranked_sites": [s.to_dict() for s in res.ranked_sites] if hasattr(res, "ranked_sites") and res.ranked_sites else [],
                "validation_result": res.validation_result.to_dict() if hasattr(res, "validation_result") and res.validation_result else {},
                "telemetry": res.telemetry,
                "test_summary": res.test_summary,
            }

        except Exception as e:
            rec = {
                "instance_id": inst_id,
                "repo": inst_id.split("__")[0].replace("__", "/"),
                "status": "ERROR",
                "error": str(e),
                "resolved": False,
                "patch_applied": False,
                "patch_valid": False,
                "patch_tier": None,
                "patch_text": "",
                "test_executed": False,
                "failure_class": FailureClass.INFRA_FAILURE.value,
                "runtime_s": round(time.time() - t0, 2),
                "f2p_passed": 0,
                "f2p_total": 0,
                "p2p_passed": 0,
                "p2p_total": 0,
                "refinement_cycles": 0,
                "target_file": "",
                "target_symbol": "",
                "telemetry": {},
                "test_summary": {},
            }

        records.append(rec)
        inst_json.write_text(json.dumps(rec, indent=2), encoding="utf-8")
        print(f"[{idx:02d}/{len(instances):02d}] DONE: {inst_id} -> Resolved: {rec['resolved']} | F2P: {rec['f2p_passed']}/{rec['f2p_total']} | P2P: {rec['p2p_passed']}/{rec['p2p_total']} | Class: {rec['failure_class']} | Time: {rec['runtime_s']}s", flush=True)

    summary_file = out_dir / "summary.json"
    resolved_count = sum(1 for r in records if r.get("resolved"))
    applied_count = sum(1 for r in records if r.get("patch_applied"))
    valid_count = sum(1 for r in records if r.get("patch_valid"))
    classes = {}
    for r in records:
        fc = r.get("failure_class", "UNKNOWN")
        classes[fc] = classes.get(fc, 0) + 1

    summary_data = {
        "total_instances": len(records),
        "resolved": resolved_count,
        "resolution_rate": round(resolved_count / len(records), 4) if records else 0,
        "patches_applied": applied_count,
        "patches_valid": valid_count,
        "failure_distribution": classes,
        "total_runtime_s": round(time.time() - t_all_start, 2),
        "instances": records,
    }
    summary_file.write_text(json.dumps(summary_data, indent=2), encoding="utf-8")

    print("\n" + "=" * 70, flush=True)
    print("COHORT EVALUATION COMPLETED", flush=True)
    print(f"Total: {len(records)} | Resolved: {resolved_count} ({summary_data['resolution_rate']*100:.1f}%)", flush=True)
    print(f"Patches Applied: {applied_count} | Valid: {valid_count}", flush=True)
    print(f"Failure Distribution: {classes}", flush=True)
    print(f"Summary written to: {summary_file}", flush=True)
    print("=" * 70 + "\n", flush=True)
    return summary_data


def main():
    parser = argparse.ArgumentParser(description="PatchForge V0.5 Cohort Evaluation")
    parser.add_argument("--instances", type=str, default="", help="Comma-separated instance IDs (default: all 20)")
    parser.add_argument("--engine", type=str, default="graph", choices=["graph", "baseline"], help="Repair engine (graph or baseline)")
    parser.add_argument("--model", type=str, default="qwen2.5-coder:14b", help="Model name")
    parser.add_argument("--workspace", type=str, default=".", help="Workspace path")
    parser.add_argument("--results-dir", type=str, default="results/v05_graph", help="Directory for JSON results")
    parser.add_argument("--max-retries", type=int, default=2, help="Max patch retries")
    parser.add_argument("--max-refinements", type=int, default=3, help="Max refinement cycles")
    parser.add_argument("--force", action="store_true", help="Force re-evaluation of completed instances")
    args = parser.parse_args()

    instances = [s.strip() for s in args.instances.split(",") if s.strip()] if args.instances else V05_COHORT_20
    run_cohort(
        instances=instances,
        model=args.model,
        engine_name=args.engine,
        workspace=args.workspace,
        results_dir=args.results_dir,
        max_retries=args.max_retries,
        max_refinements=args.max_refinements,
        force=args.force,
    )



if __name__ == "__main__":
    main()
