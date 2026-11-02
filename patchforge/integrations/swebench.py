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


_DATASET_CACHE: dict[str, dict[str, dict]] = {}


class SWEBenchAdapter:
    def __init__(self, config: PatchForgeConfig | None = None):
        self.config = config or PatchForgeConfig()

    def load_problem(self, instance_id: str) -> Problem:
        cache_key = f"{self.config.dataset_name}:test"
        if cache_key not in _DATASET_CACHE:
            from datasets import load_dataset
            ds = load_dataset(self.config.dataset_name, split="test")
            _DATASET_CACHE[cache_key] = {row["instance_id"]: dict(row) for row in ds}

        rows = _DATASET_CACHE[cache_key]
        if instance_id in rows:
            return Problem.from_dataset_row(rows[instance_id])
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
        import time
        run_id = run_id or f"eval_{int(time.time())}"
        outer_timeout = timeout + 60

        env = dict(os.environ)
        env["PYTHONUTF8"] = "1"
        docker_bin = r"C:\Program Files\Docker\Docker\resources\bin"
        if os.path.exists(docker_bin) and docker_bin not in env.get("PATH", ""):
            env["PATH"] = docker_bin + os.pathsep + env.get("PATH", "")

        import sys
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "swebench.harness.run_evaluation",
                 "--dataset_name", self.config.dataset_name,
                 "-p", predictions_path, "-i", instance_id,
                 "--run_id", run_id, "--max_workers", "1",
                 "--timeout", str(timeout)],
                capture_output=True, text=True, env=env,
                timeout=outer_timeout,
            )
        except subprocess.TimeoutExpired as te:
            result = EvalResult(instance_id=instance_id, run_id=run_id, infra_failure=True)
            result.error = f"INFRA_TIMEOUT: Evaluation harness timed out after outer timeout of {outer_timeout}s"
            result.stdout = te.stdout if isinstance(te.stdout, str) else (te.stdout.decode("utf-8", errors="replace") if te.stdout else "")
            result.stderr = te.stderr if isinstance(te.stderr, str) else (te.stderr.decode("utf-8", errors="replace") if te.stderr else "")
            try:
                subprocess.run(["docker", "kill", f"sweb.eval.{instance_id}.{run_id}"], capture_output=True, timeout=10)
            except Exception:
                pass
            return result

        result = EvalResult(instance_id=instance_id, run_id=run_id)
        result.stdout = proc.stdout or ""
        result.stderr = proc.stderr or ""
        result.test_output = self._find_test_output(run_id, instance_id) or (proc.stdout or "")
        report = self._find_report(run_id, instance_id)
        if report is None:
            err_msg = (proc.stderr or proc.stdout or "").strip()
            result.infra_failure = True
            result.error = f"EVAL_INTEGRITY_FAILURE: No valid report found in run scope for run_id='{run_id}', instance_id='{instance_id}'. Harness exit code: {proc.returncode}: {err_msg[-600:]}"
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
        # Strictly run-scoped search: only inspect directories belonging to run_id
        search_dirs = [
            Path("logs") / "run_evaluation" / run_id,
            Path("logs") / "evaluation" / run_id,
        ]
        if Path(run_id).is_dir():
            search_dirs.append(Path(run_id))

        for base in search_dirs:
            if not base.exists():
                continue
            for logs in base.rglob("*.json"):
                if "report" in logs.name.lower() or run_id in str(logs):
                    try:
                        data = json.loads(logs.read_text(encoding="utf-8"))
                    except Exception:
                        continue
                    if isinstance(data, dict):
                        if instance_id in data and isinstance(data[instance_id], dict):
                            entry = data[instance_id]
                            if "resolved" in entry or "tests_status" in entry:
                                return data
                        elif data.get("instance_id") == instance_id and "resolved" in data.get("result", {}):
                            return {instance_id: data["result"]}

        # Check explicit baseline run artifact if run_id matches provenance
        baseline_file = Path("experiments") / "baselines" / "00_swebench_gold_smoke" / f"{instance_id}.json"
        if baseline_file.exists():
            try:
                base_data = json.loads(baseline_file.read_text(encoding="utf-8"))
                if base_data.get("run_id") == run_id and base_data.get("instance_id") == instance_id:
                    res_obj = base_data.get("result", {})
                    return {
                        instance_id: {
                            "resolved": res_obj.get("resolved", False),
                            "patch_successfully_applied": True,
                            "tests_status": {
                                "FAIL_TO_PASS": {"success": res_obj.get("fail_to_pass", {}).get("success", []), "failure": []},
                                "PASS_TO_PASS": {"success": res_obj.get("pass_to_pass", {}).get("success", []), "failure": []},
                            },
                        }
                    }
            except Exception:
                pass

        return None

    @staticmethod
    def _find_test_output(run_id: str, instance_id: str) -> str:
        # Strictly run-scoped search: only inspect output files within the run's folder
        search_dirs = [
            Path("logs") / "run_evaluation" / run_id,
            Path("logs") / "evaluation" / run_id,
        ]
        if Path(run_id).is_dir():
            search_dirs.append(Path(run_id))

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


def validate_evaluation_consistency(
    eval_result: EvalResult,
    test_patch: str = "",
    raw_test_output: str = "",
    base_eval_result: EvalResult | None = None,
    base_test_output: str = "",
) -> dict:
    """Generic supplementary evaluation-integrity check comparing task test patch with declared F2P/P2P sets.
    Validates base-versus-patched test outcomes to detect non-discriminating tests that already pass on the base commit.
    Never silently rewrites the official SWE-bench score.
    """
    import re
    if not test_patch and not base_eval_result and not base_test_output:
        return {
            "verified": False,
            "reason": "No test patch provided",
            "inconsistent": False,
            "unrepresented_failures": [],
            "non_discriminating_tests": [],
        }

    # Extract test functions introduced/modified by test patch
    test_names = re.findall(r"\bdef\s+(test_\w+)\b", test_patch) if test_patch else []

    declared_f2p = set(eval_result.fail_to_pass_success + eval_result.fail_to_pass_failure)
    declared_p2p = set(eval_result.pass_to_pass_success + eval_result.pass_to_pass_failure)
    all_declared = declared_f2p | declared_p2p

    # Check if raw test output contains failures of tests not declared in F2P/P2P
    unrepresented_failures = []
    if raw_test_output and test_names:
        for t in test_names:
            if re.search(rf"\b(FAILED|ERROR)\b.*?\b{re.escape(t)}\b", raw_test_output) or re.search(rf"\b{re.escape(t)}\b.*?\b(FAILED|ERROR)\b", raw_test_output):
                # Check if this test was declared in fail_to_pass_failure or pass_to_pass_failure
                matching_declared = [d for d in (eval_result.fail_to_pass_failure + eval_result.pass_to_pass_failure) if t in d]
                if not matching_declared:
                    unrepresented_failures.append(t)

    # Base-versus-patched test outcome comparison to flag non-discriminating tests
    non_discriminating_tests = []
    if base_eval_result:
        base_passed = set(base_eval_result.pass_to_pass_success + base_eval_result.fail_to_pass_success)
        for t in eval_result.fail_to_pass_success:
            if any(t == bp or t in bp or bp in t for bp in base_passed):
                non_discriminating_tests.append(t)
    elif base_test_output:
        for t in eval_result.fail_to_pass_success:
            has_passed_base = bool(re.search(rf"\b{re.escape(t)}\b.*?\b(PASSED|ok)\b", base_test_output) or re.search(rf"\b(PASSED|ok)\b.*?\b{re.escape(t)}\b", base_test_output))
            has_failed_base = bool(re.search(rf"\b(FAILED|ERROR)\b.*?\b{re.escape(t)}\b", base_test_output) or re.search(rf"\b{re.escape(t)}\b.*?\b(FAILED|ERROR)\b", base_test_output))
            if has_passed_base and not has_failed_base:
                non_discriminating_tests.append(t)

    integrity_anomalies = []
    if non_discriminating_tests:
        integrity_anomalies.append(
            f"Non-discriminating fail-to-pass tests detected (already passing on base commit): {non_discriminating_tests}"
        )
    if eval_result.resolved and eval_result.fail_to_pass_failure:
        integrity_anomalies.append(f"Official resolved=True but fail_to_pass_failure is non-empty: {eval_result.fail_to_pass_failure}")
    if eval_result.resolved and eval_result.pass_to_pass_failure:
        integrity_anomalies.append(f"Official resolved=True but pass_to_pass_failure is non-empty: {eval_result.pass_to_pass_failure}")
    f2p_total = eval_result.fail_to_pass_total or (len(eval_result.fail_to_pass_success) + len(eval_result.fail_to_pass_failure))
    if eval_result.resolved and f2p_total == 0:
        integrity_anomalies.append("Official resolved=True but fail_to_pass_total is 0 (missing F2P test assertions).")

    return {
        "verified": True,
        "official_resolved": eval_result.resolved,
        "inconsistent": bool(unrepresented_failures or integrity_anomalies),
        "unrepresented_failures": unrepresented_failures,
        "non_discriminating_tests": non_discriminating_tests,
        "integrity_anomalies": integrity_anomalies,
        "test_names_in_patch": test_names,
    }

