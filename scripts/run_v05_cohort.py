"""20-Task Cohort Runner for PatchForge V1.6 Baseline, Graph, & V1 Repair Orchestrator.

Usage:
    python scripts/run_v05_cohort.py --engine v1 --model qwen2.5-coder:14b
    python scripts/run_v05_cohort.py --instances psf__requests-863,pallets__flask-4045 --engine v1 --force
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
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
from patchforge.pipeline.v1_orchestrator import V1RepairOrchestrator
from patchforge.verification.classifier import FailureClass
from patchforge.verification.tester import Tester


DEPENDENCY_CONFIG_FILES = [
    "pyproject.toml",
    "requirements.txt",
    "setup.py",
    "setup.cfg",
    "environment.yml",
]


def compute_source_revision_hash(workspace: str = ".") -> tuple[str, str]:
    """Returns (commit_hash, dirty_diff_hash) for tracked engine source files and dependencies.

    Deterministic rules:
    1. Computes short HEAD commit.
    2. Gathers git diff HEAD for tracked changes across patchforge/, scripts/run_v05_cohort.py,
       and root dependency manifests.
    3. Gathers untracked relevant files in patchforge/ and root dependency manifests,
       reading and hashing their actual content (not just filename or status).
    4. Normalizes all relative paths using forward slashes and sorts files deterministically.
    5. Excludes generated artifacts, caches, virtualenvs, results, and untracked docs.
    """
    ws = Path(workspace)
    try:
        head_res = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=workspace, capture_output=True, text=True)
        commit = head_res.stdout.strip() or "unknown"
    except Exception:
        commit = "unknown"

    try:
        # Check tracked files: patchforge/, run script, and any present root dependency files
        tracked_targets = ["patchforge", "scripts/run_v05_cohort.py"]
        for dep in DEPENDENCY_CONFIG_FILES:
            if (ws / dep).exists():
                tracked_targets.append(dep)

        diff_res = subprocess.run(
            ["git", "diff", "HEAD", "--"] + tracked_targets,
            cwd=workspace,
            capture_output=True,
            text=True,
        )
        diff_text = diff_res.stdout or ""

        # Check untracked relevant source files in patchforge/
        untracked_files: list[str] = []
        status_res = subprocess.run(
            ["git", "status", "--porcelain", "-uall", "patchforge/"],
            cwd=workspace,
            capture_output=True,
            text=True,
        )
        for line in (status_res.stdout or "").splitlines():
            line = line.strip()
            if line.startswith("??"):
                rel_path = line[2:].strip().strip('"').replace("\\", "/").rstrip("/")
                p = ws / rel_path
                if p.is_file():
                    if not any(part in ("__pycache__", ".pytest_cache", ".git") for part in p.parts) and not p.name.endswith((".pyc", ".pyo", ".pyd")):
                        untracked_files.append(str(p.relative_to(ws)).replace("\\", "/"))
                elif p.is_dir():
                    for child in p.rglob("*"):
                        if child.is_file() and not any(part in ("__pycache__", ".pytest_cache", ".git") for part in child.parts) and not child.name.endswith((".pyc", ".pyo", ".pyd")):
                            untracked_files.append(str(child.relative_to(ws)).replace("\\", "/"))

        # Check untracked root dependency files if any
        for dep in DEPENDENCY_CONFIG_FILES:
            p = ws / dep
            if p.is_file():
                status_dep = subprocess.run(["git", "status", "--porcelain", dep], cwd=workspace, capture_output=True, text=True)
                if (status_dep.stdout or "").strip().startswith("??"):
                    untracked_files.append(dep.replace("\\", "/"))

        # Deterministic sort
        untracked_files = sorted(set(untracked_files))

        # Hash actual content of each untracked file
        untracked_hashes: list[str] = []
        for rel_file in untracked_files:
            file_path = ws / rel_file
            try:
                content_bytes = file_path.read_bytes()
                f_hash = hashlib.sha256(content_bytes).hexdigest()[:16]
                untracked_hashes.append(f"{rel_file}:{f_hash}")
            except Exception:
                pass

        untracked_summary = "\n".join(untracked_hashes)
        combined = (diff_text + "\n" + untracked_summary).encode("utf-8")
        dirty_hash = hashlib.sha256(combined).hexdigest()[:16] if combined.strip() else "clean"
    except Exception:
        dirty_hash = "unknown"

    return commit, dirty_hash


def get_git_commit_hash(repo_dir: str = ".") -> str:
    """Returns short commit hash of current repo state."""
    commit, _ = compute_source_revision_hash(repo_dir)
    return commit


def compute_prompt_hash() -> str:
    """Computes SHA-256 fingerprint of the active repair and diagnosis prompt templates."""
    try:
        from patchforge.repair.structured_repair import SYSTEM_STRUCTURED_REPAIR_PROMPT
        from patchforge.pipeline.v1_orchestrator import SYSTEM_DIAGNOSIS_PROMPT
        combined = SYSTEM_STRUCTURED_REPAIR_PROMPT + "\n---DIAG---\n" + SYSTEM_DIAGNOSIS_PROMPT
        return hashlib.sha256(combined.encode("utf-8")).hexdigest()[:16]
    except Exception:
        return "unknown"


def compute_harness_impl_hash(workspace: Path | str = ".") -> str:
    """Computes SHA-256 fingerprint of the local evaluation harness adapter."""
    ws = Path(workspace)
    harness_file = ws / "patchforge" / "integrations" / "swebench.py"
    if harness_file.is_file():
        try:
            return hashlib.sha256(harness_file.read_bytes()).hexdigest()[:16]
        except Exception:
            return ""
    return ""


def build_evaluation_manifest(
    engine_name: str,
    model: str,
    workspace: str = ".",
    max_retries: int = 3,
    max_refinements: int = 3,
    temperature: float = 0.0,
    max_tokens: int = 2048,
    enable_dynamic_probing: bool = False,
    memory_mode: str = "FROZEN",
    memory_snapshot_fingerprint: str = "",
    cohort_name: str = "v05_cohort_20",
    cohort_instances: Optional[list[str]] = None,
    dataset_name: str = "SWE-bench/SWE-bench_Lite",
    dataset_revision: str = "main",
    datasets_version: str = "",
    task_definitions_hash: str = "",
    docker_image_tag: str = "latest",
    harness_version: str = "",
    harness_impl_hash: str = "",
    harness_timeout_s: int = 300,
    eval_harness: str = "swebench.harness.run_evaluation",
) -> dict[str, Any]:
    """Builds transparent evaluation identity manifest."""
    commit_hash, dirty_hash = compute_source_revision_hash(workspace)
    prompt_hash = compute_prompt_hash()

    if not harness_version:
        try:
            import swebench
            harness_version = f"swebench-{getattr(swebench, '__version__', '5.0.2')}"
        except Exception:
            harness_version = "swebench-unknown"

    if not datasets_version:
        try:
            import datasets
            datasets_version = f"datasets-{getattr(datasets, '__version__', 'unknown')}"
        except Exception:
            datasets_version = "datasets-unknown"

    if not harness_impl_hash:
        harness_impl_hash = compute_harness_impl_hash(workspace)

    cohort_list = sorted(cohort_instances) if cohort_instances else []
    cohort_hash = hashlib.sha256(",".join(cohort_list).encode("utf-8")).hexdigest()[:16] if cohort_list else ""

    manifest = {
        "engine_name": engine_name.lower(),
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "max_retries": max_retries,
        "max_refinements": max_refinements,
        "enable_dynamic_probing": enable_dynamic_probing,
        "commit_hash": commit_hash,
        "dirty_diff_hash": dirty_hash,
        "prompt_template_hash": prompt_hash,
        "memory_mode": memory_mode,
        "memory_snapshot_fingerprint": memory_snapshot_fingerprint,
        "cohort_name": cohort_name,
        "cohort_size": len(cohort_list),
        "cohort_hash": cohort_hash,
        "dataset_name": dataset_name,
        "dataset_revision": dataset_revision,
        "datasets_version": datasets_version,
        "task_definitions_hash": task_definitions_hash,
        "docker_image_tag": docker_image_tag,
        "harness_version": harness_version,
        "harness_impl_hash": harness_impl_hash,
        "harness_timeout_s": harness_timeout_s,
        "eval_harness": eval_harness,
    }
    return manifest


def compute_cache_fingerprint(
    manifest_or_engine: Any,
    model: str = "",
    commit_hash: str = "",
) -> str:
    """Computes SHA-256 fingerprint from evaluation manifest or legacy parameters."""
    if isinstance(manifest_or_engine, dict):
        manifest_bytes = json.dumps(manifest_or_engine, sort_keys=True).encode("utf-8")
        return hashlib.sha256(manifest_bytes).hexdigest()[:16]
    raw = f"{manifest_or_engine}:{model}:{commit_hash}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


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
    engine_name: str = "v1",
    workspace: str = ".",
    results_dir: str = "",
    max_retries: int = 3,
    max_refinements: int = 3,
    memory_mode: str = "FROZEN",
    force: bool = False,
):
    if not results_dir:
        if engine_name.lower() in ("v1", "orchestrator"):
            results_dir = "results/v1_orchestrator"
        elif engine_name.lower() == "graph":
            results_dir = "results/v05_graph"
        else:
            results_dir = "results/baseline"

    out_dir = Path(results_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    from patchforge.memory.store import CrossTaskRepairMemory, MemoryMode
    episodes_dir = str(Path(workspace) / "results" / "episodes")
    mem_store = CrossTaskRepairMemory(
        episodes_dir=episodes_dir,
        mode=MemoryMode(memory_mode) if isinstance(memory_mode, str) else memory_mode,
    )
    snapshot_fp = mem_store.snapshot_fingerprint

    cfg = PatchForgeConfig(default_model=model, workspace=workspace)
    manifest = build_evaluation_manifest(
        engine_name=engine_name,
        model=model,
        workspace=workspace,
        max_retries=max_retries,
        max_refinements=max_refinements,
        temperature=0.0,
        max_tokens=2048,
        enable_dynamic_probing=False,
        memory_mode=memory_mode,
        memory_snapshot_fingerprint=snapshot_fp,
        cohort_name="v05_cohort_20" if len(instances) == 20 else f"cohort_{len(instances)}",
        cohort_instances=instances,
        dataset_name=cfg.dataset_name,
        harness_timeout_s=cfg.max_test_seconds or 300,
        eval_harness="swebench.harness.run_evaluation",
    )
    expected_fingerprint = compute_cache_fingerprint(manifest)
    swebench = SWEBenchAdapter(cfg)
    tester = Tester(cfg, swebench)
    provider = OllamaProvider(model=model)

    records: list[dict[str, Any]] = []
    print("=" * 70, flush=True)
    print(f"PATCHFORGE V1.6 COHORT EVALUATION ({len(instances)} tasks)", flush=True)
    print(f"Engine: {engine_name.upper()} | Model: {model} | Commit: {manifest['commit_hash']} | Fingerprint: {expected_fingerprint}", flush=True)
    print(f"Results Dir: {results_dir} | Force: {force}", flush=True)
    print("=" * 70, flush=True)

    t_all_start = time.time()

    for idx, inst_id in enumerate(instances, 1):
        inst_json = out_dir / f"{inst_id}.json"
        if not force and inst_json.exists():
            try:
                rec = json.loads(inst_json.read_text(encoding="utf-8"))
                rec_fingerprint = rec.get("cache_fingerprint")
                rec_manifest = rec.get("cache_manifest")

                if not rec_fingerprint or not rec_manifest:
                    print(f"[{idx:02d}/{len(instances):02d}] {inst_id} (LEGACY CACHE MISSING MANIFEST -> RE-RUNNING)", flush=True)
                elif rec_fingerprint != expected_fingerprint:
                    diff_fields = [k for k, v in manifest.items() if rec_manifest.get(k) != v]
                    print(f"[{idx:02d}/{len(instances):02d}] {inst_id} (MANIFEST MISMATCH in {diff_fields}: run={expected_fingerprint}, cache={rec_fingerprint}) -> RE-RUNNING", flush=True)
                else:
                    print(f"[{idx:02d}/{len(instances):02d}] {inst_id} (CACHED - {rec_fingerprint})", flush=True)
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

            if engine_name.lower() in ("v1", "orchestrator"):
                engine = V1RepairOrchestrator(
                    provider=provider,
                    model_name=model,
                    workspace=workspace,
                    memory_mode=memory_mode,
                )
            elif engine_name.lower() == "graph":
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
                "engine_name": engine_name,
                "model": model,
                "commit_hash": manifest["commit_hash"],
                "cache_fingerprint": expected_fingerprint,
                "cache_manifest": manifest,
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
                "target_provenance": getattr(res, "target_provenance", {}),
                "generation_attempted": getattr(res, "generation_attempted", False),
                "structurally_valid": getattr(res, "structurally_valid", False),
                "submitted_to_harness": getattr(res, "submitted_to_harness", False),
                "test_time_s": getattr(res, "test_time_s", 0.0),
                "generation_time_s": getattr(res, "generation_time_s", 0.0),
                "reconstruction_time_s": getattr(res, "reconstruction_time_s", 0.0),
                "indexing_time_s": getattr(res, "indexing_time_s", None),
                "retrieval_diagnosis_time_s": getattr(res, "retrieval_diagnosis_time_s", None),
                "probing_time_s": getattr(res, "probing_time_s", None),
                "static_validation_time_s": getattr(res, "static_validation_time_s", 0.0),
                "replanning_time_s": getattr(res, "replanning_time_s", 0.0),
                "memory_mode": getattr(res, "memory_mode", memory_mode),
                "memory_provenance": getattr(res, "memory_provenance", {}),
                "diagnosis": res.diagnosis.to_dict() if hasattr(res, "diagnosis") and hasattr(res.diagnosis, "to_dict") else (res.diagnosis.__dict__ if hasattr(res, "diagnosis") and res.diagnosis else {}),
                "refinement_history": res.refinement_history if hasattr(res, "refinement_history") else [],
                "repair_unit": res.repair_unit.to_dict() if hasattr(res, "repair_unit") and res.repair_unit else {},
                "ranked_sites": [s.to_dict() for s in res.ranked_sites] if hasattr(res, "ranked_sites") and res.ranked_sites else [],
                "validation_result": res.validation_result.to_dict() if hasattr(res, "validation_result") and res.validation_result else {},
                "telemetry": res.telemetry,
                "test_summary": res.test_summary,
                "experiment_executed": getattr(res, "experiment_executed", False),
                "experiment_summary": getattr(res, "experiment_summary", ""),
                "memory_exemplars_used": getattr(res, "memory_exemplars_used", 0),
                "contract": res.contract.to_dict() if getattr(res, "contract", None) else {},
                "requirement_matrix": res.requirement_matrix.to_dict() if getattr(res, "requirement_matrix", None) else {},
            }

        except Exception as e:
            rec = {
                "instance_id": inst_id,
                "repo": inst_id.split("__")[0].replace("__", "/"),
                "engine_name": engine_name,
                "model": model,
                "commit_hash": manifest["commit_hash"],
                "cache_fingerprint": expected_fingerprint,
                "cache_manifest": manifest,
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
        "engine_name": engine_name,
        "model": model,
        "commit_hash": manifest["commit_hash"],
        "cache_fingerprint": expected_fingerprint,
        "cache_manifest": manifest,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
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
    parser = argparse.ArgumentParser(description="PatchForge V1.6 Cohort Evaluation")
    parser.add_argument("--instances", type=str, default="", help="Comma-separated instance IDs (default: all 20)")
    parser.add_argument("--engine", type=str, default="v1", choices=["v1", "orchestrator", "graph", "baseline"], help="Repair engine (default: v1 - Canonical V1RepairOrchestrator)")
    parser.add_argument("--model", type=str, default="qwen2.5-coder:14b", help="Model name")
    parser.add_argument("--workspace", type=str, default=".", help="Workspace path")
    parser.add_argument("--results-dir", type=str, default="", help="Directory for JSON results")
    parser.add_argument("--max-retries", type=int, default=2, help="Max patch retries")
    parser.add_argument("--max-refinements", type=int, default=3, help="Max refinement cycles")
    parser.add_argument("--memory-mode", type=str, default="FROZEN", choices=["FROZEN", "ONLINE", "DISABLED"], help="Memory mode (default: FROZEN)")
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
        memory_mode=args.memory_mode,
        force=args.force,
    )



if __name__ == "__main__":
    main()
