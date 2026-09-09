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

    def __init__(self, tester: Any = None):
        self.tester = tester

    def execute(self, args: dict[str, Any], state: Any = None) -> ToolResult:
        test_target = args.get("test_target", "")
        timeout = args.get("timeout_seconds", 60)

        # If tester is available on state, use tester container / runner
        tester = self.tester or (state.tester if state and hasattr(state, "tester") else None)
        if tester and hasattr(state, "instance_id") and state.instance_id and hasattr(state, "active_patch") and state.active_patch:
            try:
                verdict = tester.run(state.instance_id, state.active_patch.patch_text)
                return ToolResult(
                    self.name,
                    status="SUCCESS",
                    data={
                        "verdict": verdict.to_dict() if hasattr(verdict, "to_dict") else str(verdict),
                        "passed": verdict.passed if hasattr(verdict, "passed") else False,
                        "fail_to_pass_passed": getattr(verdict, "fail_to_pass_passed", 0),
                        "fail_to_pass_total": getattr(verdict, "fail_to_pass_total", 0),
                    },
                    message=f"Test executed with classification {getattr(verdict, 'classification', 'UNKNOWN')}.",
                    raw_output=getattr(verdict, "test_output", "")[:2000],
                )
            except Exception as e:
                return ToolResult(self.name, status="ERROR", error=f"Tester execution failed: {str(e)}")

        # Local pytest fallback for local test files
        repo_path = Path(state.repo_dir if state and hasattr(state, "repo_dir") else ".")
        try:
            cmd = ["pytest", test_target, "-q"]
            proc = subprocess.run(cmd, cwd=str(repo_path), capture_output=True, text=True, timeout=timeout)
            passed = proc.returncode == 0
            return ToolResult(
                self.name,
                status="SUCCESS",
                data={"passed": passed, "exit_code": proc.returncode, "stdout": proc.stdout[:1500]},
                message=f"Local test {test_target} {'PASSED' if passed else 'FAILED'}.",
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

    def execute(self, args: dict[str, Any], state: Any = None) -> ToolResult:
        repro_code = args.get("repro_code", "")
        repo_path = Path(state.repo_dir if state and hasattr(state, "repo_dir") else ".")

        import tempfile
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as tf:
                tf.write(repro_code)
                tf_name = tf.name

            proc = subprocess.run(["python", tf_name], cwd=str(repo_path), capture_output=True, text=True, timeout=15)
            try:
                Path(tf_name).unlink()
            except Exception:
                pass

            return ToolResult(
                self.name,
                status="SUCCESS",
                data={
                    "exit_code": proc.returncode,
                    "stdout": proc.stdout[:1000],
                    "stderr": proc.stderr[:1000],
                    "reproduced": proc.returncode != 0,
                },
                message=f"Reproduction script exited with code {proc.returncode}.",
                raw_output=(proc.stdout + "\n" + proc.stderr)[:1500],
            )
        except subprocess.TimeoutExpired:
            return ToolResult(self.name, status="TIMEOUT", error="Reproduction script timed out after 15s.")
        except Exception as e:
            return ToolResult(self.name, status="ERROR", error=f"Failed executing reproduction: {str(e)}")


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
