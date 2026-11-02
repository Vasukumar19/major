"""Execution and verification tools for PatchForge v0.3."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from patchforge.tools.base import Tool, ToolResult


class RunTargetedTestTool(Tool):
    name = "run_targeted_test"
    description = (
        "Execute a specific test file or test case in the test environment (or local pytest). "
        "Returns pass/fail status and output."
    )
    parameters = {
        "type": "object",
        "properties": {
            "test_target": {"type": "string", "description": "Pytest target, e.g. 'tests/test_requests.py::test_entry'"},
            "timeout_seconds": {"type": "integer", "description": "Max execution time in seconds (default 60)"},
        },
        "required": ["test_target"],
    }

    def __init__(self, tester: Any = None, allow_host_execution: bool = False):
        self.tester = tester
        self.allow_host_execution = allow_host_execution

    def execute(self, args: dict[str, Any], state: Any = None) -> ToolResult:
        test_target = args.get("test_target", "")
        timeout = args.get("timeout_seconds", 60)

        # An official SWE-bench evaluation is not a selected test run. Only
        # invoke a tester when it explicitly advertises a selector-aware,
        # isolated executor; otherwise return an actionable configuration error.
        tester = self.tester or (state.tester if state and hasattr(state, "tester") else None)
        if tester and hasattr(state, "instance_id") and state.instance_id and hasattr(state, "active_patch") and state.active_patch:
            if getattr(tester, "supports_selected_tests", False) is not True:
                return ToolResult(
                    self.name,
                    status="ERROR",
                    error="SELECTED_TEST_UNAVAILABLE: Configured tester only supports full harness evaluation; an isolated selected-test executor is required.",
                    data={"isolation": "UNAVAILABLE", "selector": test_target},
                )
            try:
                verdict = tester.run_selected_tests(
                    str(getattr(state, "repo_dir", "")), [test_target], timeout
                )
                return ToolResult(
                    self.name,
                    status="SUCCESS",
                    data={
                        "verdict": verdict.to_dict() if hasattr(verdict, "to_dict") else str(verdict),
                        "passed": not bool(getattr(verdict, "failed", ())) and not bool(getattr(verdict, "timed_out", False)),
                        "selector": test_target,
                        "isolation": "SELECTED_EXECUTOR",
                    },
                    message=f"Selected test executed through isolated executor: {test_target}.",
                    raw_output=getattr(verdict, "output", "")[:2000],
                )
            except Exception as e:
                return ToolResult(
                    self.name,
                    status="ERROR",
                    error=f"SELECTED_EXECUTION_FAILURE: Selected test executor failed: {str(e)}. Host execution fallback is disabled.",
                    data={"isolation": "SELECTED_EXECUTOR_FAILED", "allow_host_execution": False},
                )

        # Host execution guard: untrusted tests must not execute on host without explicit opt-in.
        # Fail-closed: authorization is strictly read from self.allow_host_execution configured
        # at tool initialization. Untrusted model arguments in args and agent-controlled or
        # externally supplied state objects cannot grant host execution privileges.
        allow_host = bool(self.allow_host_execution is True)

        if not allow_host:
            return ToolResult(
                self.name,
                status="ERROR",
                error=(
                    "SECURITY_POLICY: Local host test execution is disabled by default for untrusted repositories. "
                    "Safe container execution required or explicit allow_host_execution=True opt-in."
                ),
                data={"isolation": "BLOCKED", "allow_host_execution": False},
            )

        # Local pytest execution for explicitly authorized repositories
        repo_path = Path(state.repo_dir if state and hasattr(state, "repo_dir") else ".").resolve()
        if not repo_path.is_dir():
            return ToolResult(
                self.name,
                status="ERROR",
                error=f"SECURITY_POLICY: Invalid or missing repository directory: '{repo_path}'",
                data={"isolation": "BLOCKED", "allow_host_execution": True},
            )

        try:
            cmd = ["pytest", test_target, "-q"]
            proc = subprocess.run(cmd, cwd=str(repo_path), capture_output=True, text=True, timeout=timeout)
            passed = proc.returncode == 0
            return ToolResult(
                self.name,
                status="SUCCESS",
                data={
                    "passed": passed,
                    "exit_code": proc.returncode,
                    "stdout": proc.stdout[:1500],
                    "isolation": "EXPLICIT_HOST_OPT_IN",
                },
                message=f"Local host test {test_target} {'PASSED' if passed else 'FAILED'}.",
                raw_output=(proc.stdout + "\n" + proc.stderr)[:2000],
            )
        except subprocess.TimeoutExpired:
            return ToolResult(self.name, status="TIMEOUT", error=f"Test {test_target} timed out after {timeout}s.")
        except Exception as e:
            return ToolResult(self.name, status="ERROR", error=f"Failed running test: {str(e)}")


class RunReproductionTool(Tool):
    name = "run_reproduction"
    description = (
        "Execute a minimal standalone Python reproduction script to observe current bug behavior."
    )
    parameters = {
        "type": "object",
        "properties": {
            "repro_code": {"type": "string", "description": "Complete Python code for the reproduction script"},
        },
        "required": ["repro_code"],
    }

    def __init__(self, allow_host_execution: bool = False):
        self.allow_host_execution = allow_host_execution

    def execute(self, args: dict[str, Any], state: Any = None) -> ToolResult:
        repro_code = args.get("repro_code", "")
        # Fail-closed: untrusted model code must not execute directly on host by default.
        # Authorization is strictly read from self.allow_host_execution configured at tool initialization.
        # Untrusted model arguments in args and agent-controlled or externally supplied state objects
        # cannot grant host execution privileges.
        allow_host = bool(self.allow_host_execution is True)

        if not allow_host:
            return ToolResult(
                self.name,
                status="ERROR",
                error=(
                    "SECURITY_POLICY: Host execution of model-generated reproduction code is disabled by default. "
                    "An isolated container sandbox is required, or explicit allow_host_execution=True opt-in."
                ),
                data={"isolation": "BLOCKED", "allow_host_execution": False},
            )

        repo_path = Path(state.repo_dir if state and hasattr(state, "repo_dir") else ".").resolve()
        if not repo_path.is_dir():
            return ToolResult(
                self.name,
                status="ERROR",
                error=f"SECURITY_POLICY: Invalid or missing repository directory: '{repo_path}'",
                data={"isolation": "BLOCKED", "allow_host_execution": True},
            )

        import tempfile
        tf_name = None
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as tf:
                tf.write(repro_code)
                tf_name = tf.name

            proc = subprocess.run(["python", tf_name], cwd=str(repo_path), capture_output=True, text=True, timeout=15)

            stderr_text = proc.stderr or ""
            # Distinguish syntax/import crashes from actual expected defect reproduction
            syntax_or_import_error = any(err in stderr_text for err in [
                "SyntaxError", "ImportError", "ModuleNotFoundError", "IndentationError"
            ])

            reproduced = (proc.returncode != 0) and not syntax_or_import_error

            return ToolResult(
                self.name,
                status="SUCCESS",
                data={
                    "exit_code": proc.returncode,
                    "stdout": proc.stdout[:1000],
                    "stderr": proc.stderr[:1000],
                    "reproduced": reproduced,
                    "syntax_or_import_error": syntax_or_import_error,
                    "isolation": "EXPLICIT_HOST_OPT_IN",
                },
                message=f"Reproduction script exited with code {proc.returncode}{' (syntax/import error)' if syntax_or_import_error else ''}.",
                raw_output=(proc.stdout + "\n" + proc.stderr)[:1500],
            )
        except subprocess.TimeoutExpired:
            return ToolResult(self.name, status="TIMEOUT", error="Reproduction script timed out after 15s.")
        except Exception as e:
            return ToolResult(self.name, status="ERROR", error=f"Failed executing reproduction: {str(e)}")
        finally:
            if tf_name:
                try:
                    Path(tf_name).unlink(missing_ok=True)
                except Exception:
                    pass


class InspectFailureTool(Tool):
    name = "inspect_failure"
    description = (
        "Parse raw test failure output into structured execution evidence (failing test IDs, tracebacks, assertion differences)."
    )
    parameters = {
        "type": "object",
        "properties": {
            "raw_output": {"type": "string", "description": "Raw test output or traceback string"},
        },
        "required": ["raw_output"],
    }

    def execute(self, args: dict[str, Any], state: Any = None) -> ToolResult:
        raw = args.get("raw_output", "")
        if not raw and state and hasattr(state, "last_test_output"):
            raw = state.last_test_output

        # Extract tracebacks
        traceback_lines = []
        in_tb = False
        failing_tests = []
        for line in raw.splitlines():
            if "FAILED " in line or "ERROR " in line:
                failing_tests.append(line.strip())
            if "Traceback (most recent call last):" in line:
                in_tb = True
            if in_tb:
                traceback_lines.append(line)
                if line.startswith("E   ") or (len(traceback_lines) > 20 and line.strip() == ""):
                    in_tb = False

        tb_summary = "\n".join(traceback_lines[:30])
        return ToolResult(
            self.name,
            status="SUCCESS",
            data={
                "failing_tests": failing_tests,
                "traceback_snippet": tb_summary,
                "total_failures": len(failing_tests),
            },
            message=f"Extracted {len(failing_tests)} failing test cases and traceback evidence.",
            raw_output=tb_summary,
        )
