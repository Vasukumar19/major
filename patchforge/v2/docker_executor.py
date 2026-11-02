"""Docker-backed SelectedTestExecutor for V2 repair candidates.

Runs the SWE-bench test harness inside an isolated container so that
untrusted repositories are never executed on the host. All configuration
comes from environment variables; no Docker path or image name is
hard-coded here.

Distinguishes:
- SETUP_FAILURE: Container mount, collection, or syntax setup failure (exit != 0, 1)
- TIMEOUT: Container execution exceeded allowed deadline
- TEST_FAILURE: Target tests executed inside container and assertions failed (exit == 1)
- EXECUTOR_FAILURE: Host docker CLI or daemon execution error

Environment variables
---------------------
PATCHFORGE_DOCKER_COMMAND   Path/name of the Docker CLI (default: shutil.which("docker")).
PATCHFORGE_DOCKER_IMAGE     SWE-bench evaluation image tag to use.
SWEBENCH_HARNESS_TIMEOUT    Seconds before a container run is killed (default: 300).
"""
from __future__ import annotations

import logging
import os
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .candidates import TestObservation
from .workspaces import OwnedWorkspace

logger = logging.getLogger(__name__)

SELECTED_TEST_UNAVAILABLE = "SELECTED_TEST_UNAVAILABLE"
_DEFAULT_TIMEOUT = 300


def _docker_binary() -> str | None:
    cmd = os.getenv("PATCHFORGE_DOCKER_COMMAND", "").strip()
    if cmd:
        return shlex.split(cmd)[0]
    import shutil
    return shutil.which("docker")


@dataclass
class DockerSelectedTestExecutor:
    """Run selected F2P/P2P tests inside an isolated SWE-bench container.

    Parameters
    ----------
    image_tag:
        Docker image that contains the SWE-bench harness and the
        repository environment. Read from ``PATCHFORGE_DOCKER_IMAGE``
        when not supplied explicitly.
    timeout_seconds:
        Override the per-run timeout. When ``None`` the value comes from
        ``SWEBENCH_HARNESS_TIMEOUT`` or the 300-second default.
    extra_docker_args:
        Additional ``docker run`` flags (e.g. ``["--network=none"]``).
    """

    image_tag: str = field(default="")
    timeout_seconds: int | None = None
    extra_docker_args: list[str] = field(default_factory=list)

    def run_selected(
        self,
        workspace: OwnedWorkspace,
        selectors: list[str],
        timeout_seconds: int,
    ) -> TestObservation:
        """Execute *selectors* inside the Docker harness; return a TestObservation."""
        if not selectors:
            return TestObservation(
                phase="F2P",
                selectors=(),
                output=SELECTED_TEST_UNAVAILABLE + ": no selectors supplied",
                exit_code=-1,
                failure_kind="SETUP_FAILURE",
            )

        docker_bin = _docker_binary()
        if not docker_bin:
            logger.warning(
                "docker not found; set PATCHFORGE_DOCKER_COMMAND. "
                "Returning SELECTED_TEST_UNAVAILABLE."
            )
            return TestObservation(
                phase="F2P",
                selectors=tuple(selectors),
                output=SELECTED_TEST_UNAVAILABLE + ": docker not found on PATH or PATCHFORGE_DOCKER_COMMAND",
                exit_code=-1,
                failure_kind="EXECUTOR_FAILURE",
            )

        image = self.image_tag or os.getenv("PATCHFORGE_DOCKER_IMAGE", "").strip()
        if not image:
            return TestObservation(
                phase="F2P",
                selectors=tuple(selectors),
                output=SELECTED_TEST_UNAVAILABLE + ": no Docker image configured (set PATCHFORGE_DOCKER_IMAGE)",
                exit_code=-1,
                failure_kind="SETUP_FAILURE",
            )

        effective_timeout = (
            self.timeout_seconds
            if self.timeout_seconds is not None
            else int(os.getenv("SWEBENCH_HARNESS_TIMEOUT", str(_DEFAULT_TIMEOUT)))
        )

        workspace_root = str(workspace.root)
        cmd: list[str] = [
            docker_bin,
            "run",
            "--rm",
            "-v",
            f"{workspace_root}:/testbed:ro",
            *self.extra_docker_args,
            image,
            "sh",
            "-c",
            'PATH=/opt/miniconda3/envs/testbed/bin:$PATH pytest "$@"',
            "pytest_runner",
            *selectors,
            "-q",
            "--tb=short",
        ]

        logger.debug("Docker executor command: %s", " ".join(cmd))

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=effective_timeout,
            )
        except subprocess.TimeoutExpired as exc:
            stdout = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
            stderr = (exc.stderr or "") if isinstance(exc.stderr, str) else ""
            output = (stdout + "\n" + stderr)[-4000:].strip()
            return TestObservation(
                phase="F2P",
                selectors=tuple(selectors),
                output=output,
                timed_out=True,
                failure_kind="TIMEOUT",
            )
        except OSError as exc:
            return TestObservation(
                phase="F2P",
                selectors=tuple(selectors),
                output=f"OSError launching Docker: {exc}",
                exit_code=-1,
                failure_kind="EXECUTOR_FAILURE",
            )

        output = (proc.stdout + "\n" + proc.stderr)[-4000:].strip()
        passed, failed = _parse_pytest_outcomes(output, selectors)

        all_known = set(passed) | set(failed)
        missing = [s for s in selectors if not any(s in k for k in all_known)]

        # Classify failure kind
        if proc.returncode == 0:
            failure_kind = None
        elif proc.returncode == 1:
            failure_kind = "TEST_FAILURE"
        else:
            failure_kind = "SETUP_FAILURE"

        if missing and proc.returncode not in (0, 1):
            unavail_msg = (
                SELECTED_TEST_UNAVAILABLE
                + f": selectors {missing!r} not collected; exit={proc.returncode}; "
                + output[-800:]
            )
            return TestObservation(
                phase="F2P",
                selectors=tuple(selectors),
                output=unavail_msg,
                exit_code=proc.returncode,
                failure_kind="SETUP_FAILURE",
            )

        return TestObservation(
            phase="F2P",
            selectors=tuple(selectors),
            passed=tuple(passed),
            failed=tuple(failed),
            output=output,
            exit_code=proc.returncode,
            failure_kind=failure_kind,
        )


def _parse_pytest_outcomes(
    output: str, selectors: list[str]
) -> tuple[list[str], list[str]]:
    """Parse pytest -q output into (passed, failed) test-id lists."""
    passed: list[str] = []
    failed: list[str] = []
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("PASSED "):
            passed.append(line[7:].strip())
        elif line.startswith("FAILED ") or line.startswith("ERROR "):
            prefix_len = 7 if line.startswith("FAILED ") else 6
            entry = line[prefix_len:].strip()
            test_id = entry.split(" - ")[0].strip()
            failed.append(test_id)
    return passed, failed
