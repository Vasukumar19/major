"""Thin adapter over the SWE-bench harness (black box, subprocess only).

Exposes: instance metadata (allowlisted), repo checkout at base_commit,
predictions submission, official evaluation, normalized result.
NEVER exposes: gold patch, reference solution, hidden test content.
"""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from patchforge.core.config import PatchForgeConfig
from patchforge.issue.problem import Problem


@dataclass
class EvalResult:
    instance_id: str = ""
    resolved: bool = False
    patch_applied: bool = False
    fail_to_pass_passed: int = 0
    fail_to_pass_total: int = 0
    pass_to_pass_passed: int = 0
    pass_to_pass_total: int = 0
    infra_failure: bool = False
    error: str = ""
    run_id: str = ""
    fail_to_pass_success: list[str] = field(default_factory=list)
    fail_to_pass_failure: list[str] = field(default_factory=list)
    pass_to_pass_success: list[str] = field(default_factory=list)
    pass_to_pass_failure: list[str] = field(default_factory=list)
    test_output: str = ""
    stdout: str = ""
    stderr: str = ""

    def to_dict(self) -> dict:
        return {
            "instance_id": self.instance_id,
            "resolved": self.resolved,
            "patch_applied": self.patch_applied,
            "fail_to_pass": {"passed": self.fail_to_pass_passed, "total": self.fail_to_pass_total},
            "pass_to_pass": {"passed": self.pass_to_pass_passed, "total": self.pass_to_pass_total},
            "fail_to_pass_success": self.fail_to_pass_success,
            "fail_to_pass_failure": self.fail_to_pass_failure,
            "pass_to_pass_success": self.pass_to_pass_success,
            "pass_to_pass_failure": self.pass_to_pass_failure,
            "infra_failure": self.infra_failure,
            "error": self.error,
            "run_id": self.run_id,
        }


class SWEBenchAdapter:
    def __init__(self, config: PatchForgeConfig | None = None):
        self.config = config or PatchForgeConfig()

    def load_problem(self, instance_id: str) -> Problem:
        from datasets import load_dataset

        ds = load_dataset(self.config.dataset_name, split="test")
        for row in ds:
            if row["instance_id"] == instance_id:
                return Problem.from_dataset_row(dict(row))
        raise KeyError(f"Instance not found: {instance_id}")

    def ensure_checkout(self, problem: Problem, repos_dir: str) -> str:
        """Clone (if needed) + checkout base_commit + verify clean. Returns repo dir."""
        dest = str((Path(repos_dir) / problem.repo.replace("/", "__")).resolve())
        if not Path(dest, ".git").exists():
            subprocess.run(
                ["git", "clone", f"https://github.com/{problem.repo}.git", dest],
                check=True,
            )
        subprocess.run(["git", "checkout", "-f", problem.base_commit], cwd=dest, check=True)
        subprocess.run(["git", "clean", "-fd"], cwd=dest, check=True)
        status = subprocess.run(
            ["git", "status", "--short"], cwd=dest, capture_output=True, text=True, check=True
        )
        if status.stdout.strip():
            raise RuntimeError(f"Repo not clean after checkout: {dest}\n{status.stdout}")
        return dest

    def write_predictions(self, instance_id: str, patch_text: str, model_name: str, path: str) -> str:
        preds = [{
            "instance_id": instance_id,
            "model_patch": patch_text,
            "model_name_or_path": model_name,
        }]
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(preds), encoding="utf-8", newline="\n")
        return path

    def evaluate(self, predictions_path: str, instance_id: str, run_id: str,
                 timeout: int = 300) -> EvalResult:
        env = dict(os.environ)
        env["PYTHONUTF8"] = "1"
        docker_bin = r"C:\Program Files\Docker\Docker\resources\bin"
        if os.path.exists(docker_bin) and docker_bin not in env.get("PATH", ""):
            env["PATH"] = docker_bin + os.pathsep + env.get("PATH", "")

        import sys
        proc = subprocess.run(
            [sys.executable, "-m", "swebench.harness.run_evaluation",
             "--dataset_name", self.config.dataset_name,
             "-p", predictions_path, "-i", instance_id,
             "--run_id", run_id, "--max_workers", "1",
             "--timeout", str(timeout)],
            capture_output=True, text=True, env=env,
        )
        result = EvalResult(instance_id=instance_id, run_id=run_id)
        result.stdout = proc.stdout or ""
        result.stderr = proc.stderr or ""
        result.test_output = self._find_test_output(run_id, instance_id) or (proc.stdout or "")
        report = self._find_report(run_id, instance_id)
        if report is None:
            err_msg = (proc.stderr or proc.stdout or "").strip()
            result.error = f"no report; harness rc={proc.returncode}: {err_msg[-600:]}"
            return result
        entry = report.get(instance_id, {})
        result.patch_applied = bool(entry.get("patch_successfully_applied", False))
        result.resolved = bool(entry.get("resolved", False))
        result.infra_failure = bool(entry.get("infra_failure", False))
        tests = entry.get("tests_status") or {}
        f2p = tests.get("FAIL_TO_PASS") or {}
        p2p = tests.get("PASS_TO_PASS") or {}
        f2p_succ = f2p.get("success", []) or []
        f2p_fail = f2p.get("failure", []) or []
        p2p_succ = p2p.get("success", []) or []
        p2p_fail = p2p.get("failure", []) or []
        result.fail_to_pass_success = list(f2p_succ)
        result.fail_to_pass_failure = list(f2p_fail)
        result.pass_to_pass_success = list(p2p_succ)
        result.pass_to_pass_failure = list(p2p_fail)
        result.fail_to_pass_passed = len(f2p_succ)
        result.fail_to_pass_total = len(f2p_succ) + len(f2p_fail)
        result.pass_to_pass_passed = len(p2p_succ)
        result.pass_to_pass_total = len(p2p_succ) + len(p2p_fail)
        return result

    @staticmethod
    def _find_report(run_id: str, instance_id: str) -> dict | None:
        search_dirs = [Path("logs") / "evaluation" / run_id, Path("logs") / "run_evaluation" / run_id, Path("logs"), Path(".")]
        for base in search_dirs:
            if not base.exists():
                continue
            for logs in base.rglob("*.json"):
                if "report" in logs.name.lower() or run_id in str(logs):
                    try:
                        data = json.loads(logs.read_text(encoding="utf-8"))
                    except Exception:
                        continue
                    if isinstance(data, dict) and instance_id in data:
                        return data
        return None

    @staticmethod
    def _find_test_output(run_id: str, instance_id: str) -> str:
        search_dirs = [
            Path("logs") / "run_evaluation" / run_id,
            Path("logs") / "evaluation" / run_id,
            Path("logs"),
        ]
        for base in search_dirs:
            if not base.exists():
                continue
            for p in base.rglob("test_output.txt"):
                if instance_id in str(p):
                    try:
                        return p.read_text(encoding="utf-8", errors="replace")
                    except Exception:
                        continue
        return ""
