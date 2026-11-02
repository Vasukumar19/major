"""Selected-test execution contracts and V2 candidate acceptance policy."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .candidates import TestObservation
from .workspaces import OwnedWorkspace


class SelectedTestExecutor(Protocol):
    def run_selected(self, workspace: OwnedWorkspace, selectors: list[str], timeout_seconds: int) -> TestObservation: ...


@dataclass
class LocalSelectedTestExecutor:
    """Explicitly opt-in local executor for trusted development repositories only."""

    allow_host_execution: bool = False

    def run_selected(self, workspace: OwnedWorkspace, selectors: list[str], timeout_seconds: int) -> TestObservation:
        if not self.allow_host_execution:
            raise PermissionError("Selected host tests are disabled; provide an isolated executor")
        if not selectors:
            raise ValueError("At least one test selector is required")
        try:
            proc = subprocess.run(
                ["python", "-m", "pytest", *selectors, "-q"],
                cwd=workspace.root,
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            return TestObservation(
                phase="F2P",
                selectors=tuple(selectors),
                output=((exc.stdout or "") + "\n" + (exc.stderr or ""))[-4000:],
                timed_out=True,
            )
        output = (proc.stdout + "\n" + proc.stderr)[-4000:]
        failed = tuple(line.strip() for line in output.splitlines() if line.startswith(("FAILED ", "ERROR ")))
        return TestObservation(
            phase="F2P",
            selectors=tuple(selectors),
            passed=tuple(selectors) if proc.returncode == 0 else (),
            failed=failed or (() if proc.returncode == 0 else tuple(selectors)),
            output=output,
            exit_code=proc.returncode,
        )


def accepts_residual_candidate(observation: TestObservation, regression: TestObservation | None = None) -> bool:
    """A candidate may become a refinement parent only if it made F2P progress
    and has no observed regression. Final acceptance remains the official gate.
    """
    if observation.timed_out or not observation.passed:
        return False
    return regression is None or (not regression.failed and not regression.timed_out)
