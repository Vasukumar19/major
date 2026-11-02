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
- PATCHFORGE_DOCKER_IMAGE must be set if selected-test execution is desired.
- Ollama must be serving the requested model.
- Docker must be running if you want the container-backed test step.

The script aborts with a clear message if required services are unavailable
rather than silently manufacturing results.
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

from patchforge.runtime import RuntimeEnvironment
from patchforge.v2.runner import V2RepairRunner, V2RunnerConfig

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("pilot")


def _load_problem(instance_id: str) -> object:
    """Attempt to load the frozen SWE-bench task as a Problem object.

    Falls back to a minimal stub when the benchmark dataset is not
    accessible — the V2 runner and V1 pipeline still execute, they just
    won't have the issue text.
    """
    # Try the frozen dataset shipped with the project
    dataset_paths = [
        Path(__file__).resolve().parents[1] / "data" / "swebench_tasks.json",
        Path(__file__).resolve().parents[1] / "data" / "swebench_20_tasks.json",
        Path(__file__).resolve().parents[1] / "data" / "frozen_tasks.json",
    ]
    for dpath in dataset_paths:
        if dpath.is_file():
            try:
                with dpath.open(encoding="utf-8") as fh:
                    tasks = json.load(fh)
                if isinstance(tasks, list):
                    task_map = {t.get("instance_id", t.get("id", "")): t for t in tasks}
                elif isinstance(tasks, dict):
                    task_map = tasks
                else:
                    continue
                if instance_id in task_map:
                    task = task_map[instance_id]
                    return _task_to_problem(task)
            except (OSError, json.JSONDecodeError, KeyError):
                continue

    logger.warning(
        "No frozen dataset found for %s — using minimal stub Problem. "
        "Place the SWE-bench task JSON in data/swebench_tasks.json for full execution.",
        instance_id,
    )
    return _stub_problem(instance_id)


def _task_to_problem(task: dict) -> object:
    """Convert a SWE-bench task dict into a Problem object."""
    try:
        from patchforge.issue.problem import Problem
        return Problem(
            instance_id=task.get("instance_id", ""),
            description=task.get("problem_statement", task.get("issue_text", "")),
            repo=task.get("repo", ""),
            base_commit=task.get("base_commit", ""),
            hints=task.get("hints_text", ""),
            fail_to_pass=task.get("FAIL_TO_PASS", []),
            pass_to_pass=task.get("PASS_TO_PASS", []),
        )
    except (ImportError, TypeError):
        return _stub_problem(task.get("instance_id", ""))


def _stub_problem(instance_id: str) -> object:
    """Minimal Problem stub when the real one cannot be constructed."""
    try:
        from patchforge.issue.problem import Problem
        return Problem(instance_id=instance_id, description="", repo="", base_commit="")
    except (ImportError, TypeError):
        # Return a plain namespace as a last resort
        import types
        p = types.SimpleNamespace()
        p.instance_id = instance_id
        p.description = ""
        p.repo = ""
        p.base_commit = ""
        p.fail_to_pass = []
        p.pass_to_pass = []
        return p


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
            "\nWARNING: Docker is not ready — the selected-test step will be skipped. "
            "Set PATCHFORGE_DOCKER_COMMAND or PATCHFORGE_DOCKER_IMAGE for container execution."
        )


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
        help="Path to a clean repo checkout at the base commit. "
        "If omitted, the runner uses the current working directory.",
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
        help="Pytest-style test selectors for the selected-test step.",
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

    # ---- Runtime check --------------------------------------------------------
    env = RuntimeEnvironment.from_environment()
    _check_and_abort_if_not_ready(env)

    # ---- Build config ---------------------------------------------------------
    cfg = V2RunnerConfig.from_environment(
        model=args.model,
        max_refinement_cycles=args.max_refinements,
        results_dir=args.results_dir,
    )

    # ---- Load problem ---------------------------------------------------------
    print(f"\nLoading task: {args.instance}")
    problem = _load_problem(args.instance)
    print(f"  Description: {str(getattr(problem, 'description', ''))[:120] or '(none)'}")

    # ---- Determine repo dir --------------------------------------------------
    repo_dir = Path(args.repo_dir).resolve() if args.repo_dir else Path.cwd()
    if not repo_dir.is_dir():
        print(f"ABORT: --repo-dir does not exist: {repo_dir}", file=sys.stderr)
        return 1
    print(f"  Repo dir   : {repo_dir}")

    # ---- Run -----------------------------------------------------------------
    print("\nStarting V2 repair runner …\n")
    runner = V2RepairRunner(cfg)
    report = runner.run_task(
        args.instance,
        repo_dir,
        problem=problem,
        test_selectors=args.selectors,
        results_dir=args.results_dir,
    )

    # ---- Print factual summary -----------------------------------------------
    print("\n" + "=" * 60)
    print(report.summary())
    print("=" * 60)

    return 0 if report.resolved else 1


if __name__ == "__main__":
    raise SystemExit(main())
