"""Single task runner for PatchForge AI supporting baseline and agentic modes.

Usage:
    python scripts/run_task.py --instance psf__requests-863 --mode baseline --model qwen2.5-coder:7b
    python scripts/run_task.py --instance psf__requests-863 --mode agent --model qwen2.5-coder:7b
"""
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

from patchforge.agent.controller import AgentController
from patchforge.agent.policy import AgentPolicy
from patchforge.core.config import PatchForgeConfig
from patchforge.core.target import RepairTarget
from patchforge.integrations.swebench import SWEBenchAdapter
from patchforge.models.provider import OllamaProvider
from patchforge.pipeline.baseline import BaselineRepairEngine
from patchforge.tools import create_default_tool_registry
from patchforge.verification.classifier import FailureClass
from patchforge.verification.tester import Tester


def run_baseline_mode(args, problem, repo_dir, tester):
    provider = OllamaProvider(model=args.model)
    engine = BaselineRepairEngine(provider=provider, model_name=args.model, workspace=args.workspace)
    result = engine.run(
        problem=problem,
        repo_dir=repo_dir,
        tester=tester,
        max_retries=args.max_retries,
        max_refinements=args.max_refinements,
    )
    return {
        "mode": "baseline",
        "instance_id": result.instance_id,
        "target": result.target.to_dict() if result.target else None,
        "patch_applied": result.patch_applied,
        "patch_valid": result.patch.valid if result.patch else False,
        "patch_tier": result.patch.match_tier if result.patch else None,
        "patch_text": result.patch.patch_text if result.patch else "",
        "test_executed": result.test_executed,
        "resolved": result.resolved,
        "failure_class": result.failure_class,
        "runtime_s": result.runtime_s,
        "telemetry": result.telemetry,
        "test_summary": result.test_summary,
        "refinement_cycles": result.refinement_cycles,
        "refinement_history": result.refinement_history,
    }


def run_agent_mode(args, problem, repo_dir, tester):
    t0 = time.time()
    provider = OllamaProvider(model=args.model)
    tools = create_default_tool_registry(repo_dir=repo_dir)
    policy = AgentPolicy(max_turns=args.max_turns)
    controller = AgentController(provider=provider, tools=tools, policy=policy)

    patch, trajectory, final_state = controller.run(problem=problem, repo_dir=repo_dir)
    runtime_s = round(time.time() - t0, 2)

    patch_applied = patch is not None and patch.valid
    test_executed = False
    resolved = False
    failure_class = FailureClass.UNRESOLVED.value
    test_summary = {}

    if patch_applied and tester:
        verdict = tester.run(problem.instance_id, patch.patch_text)
        test_executed = True
        resolved = bool(getattr(verdict, "resolved", False))
        test_summary = verdict.to_dict() if hasattr(verdict, "to_dict") else {}
        if resolved:
            failure_class = FailureClass.RESOLVED.value
        elif getattr(verdict, "infra_failure", False):
            failure_class = FailureClass.INFRA_FAILURE.value
        else:
            failure_class = FailureClass.TEST_FAILURE.value

    return {
        "mode": "agent",
        "instance_id": problem.instance_id,
        "turns_used": final_state.turn_count,
        "max_turns": args.max_turns,
        "patch_applied": patch_applied,
        "patch_valid": patch.valid if patch else False,
        "patch_tier": patch.match_tier if patch else None,
        "patch_text": patch.patch_text if patch else "",
        "test_executed": test_executed,
        "resolved": resolved,
        "failure_class": failure_class,
        "runtime_s": runtime_s,
        "guard_firings": final_state.guard_firings,
        "trajectory_steps": len(trajectory.steps),
        "test_summary": test_summary,
    }


def main():
    parser = argparse.ArgumentParser(description="PatchForge AI Single Task Runner")
    parser.add_argument("--instance", type=str, default="psf__requests-863", help="SWE-bench instance ID")
    parser.add_argument("--mode", type=str, choices=["baseline", "agent"], default="baseline", help="Execution mode")
    parser.add_argument("--model", type=str, default="qwen2.5-coder:7b", help="Model identifier")
    parser.add_argument("--workspace", type=str, default=".", help="Workspace path")
    parser.add_argument("--max-turns", type=int, default=8, help="Max turns for agent mode")
    parser.add_argument("--max-retries", type=int, default=2, help="Max patch retries for baseline mode")
    parser.add_argument("--max-refinements", type=int, default=2, help="Max refinement cycles for baseline mode")
    parser.add_argument("--output-json", type=str, default="", help="Path to save output JSON")
    args = parser.parse_args()

    cfg = PatchForgeConfig(default_model=args.model, workspace=args.workspace)
    swebench = SWEBenchAdapter(cfg)
    tester = Tester(cfg, swebench)

    print(f"=== PatchForge Task Execution: {args.instance} [{args.mode.upper()}] ===", flush=True)
    problem = swebench.load_problem(args.instance)
    repos_dir = str(Path(args.workspace) / "experiments" / "phase_d_v03" / "repos")
    repo_dir = swebench.ensure_checkout(problem, repos_dir)

    if args.mode == "baseline":
        summary = run_baseline_mode(args, problem, repo_dir, tester)
    else:
        summary = run_agent_mode(args, problem, repo_dir, tester)

    print("\n" + "=" * 60, flush=True)
    print(f"EXECUTION SUMMARY ({summary['mode']}):", flush=True)
    print(f"Instance ID:    {summary['instance_id']}", flush=True)
    print(f"Patch Applied:  {summary['patch_applied']}", flush=True)
    print(f"Patch Valid:    {summary['patch_valid']}", flush=True)
    print(f"Test Executed:  {summary['test_executed']}", flush=True)
    print(f"Resolved:       {summary['resolved']}", flush=True)
    print(f"Failure Class:  {summary['failure_class']}", flush=True)
    print(f"Refinement:     {summary.get('refinement_cycles', 0)} cycles", flush=True)
    if summary.get("test_summary"):
        ts = summary["test_summary"]
        print(f"FAIL_TO_PASS:   {ts.get('fail_to_pass_passed', 0)}/{ts.get('fail_to_pass_total', 0)}", flush=True)
        print(f"PASS_TO_PASS:   {ts.get('pass_to_pass_passed', 0)}/{ts.get('pass_to_pass_total', 0)}", flush=True)
    print(f"Runtime:        {summary['runtime_s']}s", flush=True)
    print("=" * 60, flush=True)

    if args.output_json:
        out_p = Path(args.output_json)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"Results written to {args.output_json}", flush=True)


if __name__ == "__main__":
    main()
