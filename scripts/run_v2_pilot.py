#!/usr/bin/env python3
"""Run the V2 repair pilot on a single SWE-bench task.

Usage
-----
  python scripts/run_v2_pilot.py --instance psf__requests-2148 \
      --model qwen2.5-coder:14b \
      [--repo-dir /path/to/checkout] \
      [--results-dir results/v2_pilot]

Prerequisites
-------------
- PATCHFORGE_OLLAMA_URL must be set (e.g. http://localhost:11434).
- PATCHFORGE_DOCKER_IMAGE can be set to override the evaluation image.
- Ollama must be serving the requested model.
- Docker must be running for container-backed testing.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

# Allow running from the repo root without installation
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from patchforge.core.config import PatchForgeConfig
from patchforge.integrations.swebench import SWEBenchAdapter
from patchforge.runtime import RuntimeEnvironment
from patchforge.v2.runner import V2RepairRunner, V2RunnerConfig
from patchforge.verification.tester import Tester

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("pilot")


def _load_problem(instance_id: str) -> object:
    """Load the SWE-bench task via SWEBenchAdapter or fallback files."""
    try:
        cfg = PatchForgeConfig()
        adapter = SWEBenchAdapter(cfg)
        return adapter.load_problem(instance_id)
    except Exception as exc:
        logger.info("SWEBenchAdapter lookup (%s) — falling back to local files", exc)

    # Fallback to local json files
    dataset_paths = [
        Path("results/episodes") / f"{instance_id}.json",
        Path("results/v19_orchestrator") / f"{instance_id}.json",
        Path("data/swebench_tasks.json"),
    ]
    for dpath in dataset_paths:
        if dpath.is_file():
            try:
                with dpath.open(encoding="utf-8") as fh:
                    task = json.load(fh)
                if isinstance(task, dict) and task.get("instance_id") == instance_id:
                    from patchforge.issue.problem import Problem
                    return Problem(
                        instance_id=instance_id,
                        description=task.get("problem_statement", task.get("issue_text", "")),
                        repo=task.get("repo", ""),
                        base_commit=task.get("base_commit", ""),
                        hints=task.get("hints_text", ""),
                        fail_to_pass=task.get("FAIL_TO_PASS", task.get("fail_to_pass", [])),
                        pass_to_pass=task.get("PASS_TO_PASS", task.get("pass_to_pass", [])),
                    )
            except Exception:
                continue

    logger.warning("No local problem record found for %s — creating stub", instance_id)
    from patchforge.issue.problem import Problem
    return Problem(instance_id=instance_id, description="", repo="", base_commit="")


def _check_and_abort_if_not_ready(env: RuntimeEnvironment) -> None:
    """Probe Ollama (required) and Docker (optional) — abort if Ollama is down."""
    ollama = env.probe_ollama()
    docker = env.probe_docker()
    print(f"  Ollama : {'READY' if ollama.ready else 'NOT READY'} — {ollama.detail}")
    print(f"  Docker : {'READY' if docker.ready else 'NOT READY'} — {docker.detail}")
    if not ollama.ready:
        print(
            "\nABORT: Ollama is not ready. "
            "Set PATCHFORGE_OLLAMA_URL and ensure 'ollama serve' is running.",
            file=sys.stderr,
        )
        sys.exit(1)
    if not docker.ready:
        print(
            "\nWARNING: Docker is not ready — selected-test and official verification will be skipped."
        )


def _resolve_repo_dir(problem: Any, cli_repo_dir: str | None) -> Path:
    """Resolve checkout directory for the problem."""
    if cli_repo_dir:
        p = Path(cli_repo_dir).resolve()
        if p.is_dir():
            return p

    # Check known local repos
    repo_name = getattr(problem, "repo", "").replace("/", "__")
    candidate_paths = [
        Path("experiments/phase_d_v03/repos") / repo_name,
        Path("repos") / repo_name,
    ]
    for cp in candidate_paths:
        if (cp / ".git").is_dir() or cp.is_dir():
            # If base commit is specified and repo is a git checkout, ensure checkout at base_commit
            base_commit = getattr(problem, "base_commit", "")
            if base_commit and (cp / ".git").is_dir():
                import subprocess
                try:
                    subprocess.run(
                        ["git", "checkout", "-f", base_commit],
                        cwd=str(cp),
                        capture_output=True,
                        check=True,
                    )
                    logger.info("Checked out %s at base commit %s", cp, base_commit[:8])
                except Exception as exc:
                    logger.warning("Could not checkout base commit %s: %s", base_commit, exc)
            return cp.resolve()

    return Path.cwd().resolve()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the V2 repair pilot on a single SWE-bench task."
    )
    parser.add_argument(
        "--instance",
        required=True,
        metavar="INSTANCE_ID",
        help="SWE-bench instance ID (e.g. psf__requests-2148)",
    )
    parser.add_argument(
        "--model",
        default=os.getenv("PATCHFORGE_MODEL", "qwen2.5-coder:14b"),
        help="Ollama model name (default: qwen2.5-coder:14b)",
    )
    parser.add_argument(
        "--repo-dir",
        default=None,
        metavar="PATH",
        help="Path to a clean repo checkout at the base commit.",
    )
    parser.add_argument(
        "--results-dir",
        default="results/v2_pilot",
        help="Directory to write the JSON report (default: results/v2_pilot)",
    )
    parser.add_argument(
        "--selectors",
        nargs="*",
        default=None,
        metavar="SELECTOR",
        help="Pytest-style test selectors for targeted residual execution.",
    )
    parser.add_argument(
        "--max-refinements",
        type=int,
        default=3,
        help="Maximum V2 refinement cycles (default: 3)",
    )
    args = parser.parse_args(argv)

    print("=" * 60)
    print(f"PatchForge V2 Pilot — {args.instance}")
    print("=" * 60)

    # Runtime probe
    env = RuntimeEnvironment.from_environment()
    _check_and_abort_if_not_ready(env)

    # Load problem
    print(f"\nLoading task: {args.instance}")
    problem = _load_problem(args.instance)
    repo_dir = _resolve_repo_dir(problem, args.repo_dir)
    print(f"  Repository : {getattr(problem, 'repo', 'unknown')}")
    print(f"  Base Commit: {getattr(problem, 'base_commit', 'unknown')}")
    print(f"  Repo dir   : {repo_dir}")

    # Build config & runner
    cfg = V2RunnerConfig.from_environment(
        model=args.model,
        max_refinement_cycles=args.max_refinements,
        results_dir=args.results_dir,
    )

    tester = None
    if env.probe_docker().ready:
        try:
            app_cfg = PatchForgeConfig(default_model=args.model)
            swebench_adapter = SWEBenchAdapter(app_cfg)
            tester = Tester(app_cfg, swebench_adapter, model_name=args.model)
        except Exception as exc:
            logger.warning("Could not initialize Tester for official evaluation: %s", exc)

    runner = V2RepairRunner(config=cfg, tester=tester)

    print("\nStarting V2 repair runner …\n")
    report = runner.run_task(
        args.instance,
        repo_dir,
        problem=problem,
        test_selectors=args.selectors,
        results_dir=args.results_dir,
        tester=tester,
    )

    print("\n" + "=" * 60)
    print(report.summary())
    print("=" * 60)
    trace_path = Path(args.results_dir) / f"{report.instance_id}_trace.md"
    if trace_path.exists():
        print(f"\nRepair trace written to: {trace_path}")

    return 0 if report.resolved else 1


if __name__ == "__main__":
    raise SystemExit(main())
