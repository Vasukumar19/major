"""Docker-backed SelectedTestExecutor for V2 repair candidates.

Runs the SWE-bench test harness inside an isolated container so that
untrusted repositories are never executed on the host. All configuration
comes from environment variables; no Docker path or image name is
hard-coded here.

Environment variables
---------------------
PATCHFORGE_DOCKER_COMMAND   Path/name of the Docker CLI (default: shutil.which("docker")).
PATCHFORGE_DOCKER_IMAGE     SWE-bench evaluation image tag to use.
SWEBENCH_HARNESS_TIMEOUT    Seconds before a container run is killed (default: 300).
"""
from __future__ import annotations

import json
import logging
import os
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .candidates import TestObservation
from .workspaces import OwnedWorkspace

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Sentinel returned instead of raising when Docker is unavailable or when
# a requested selector is not found in the harness output.
# ---------------------------------------------------------------------------
SELECTED_TEST_UNAVAILABLE = "SELECTED_TEST_UNAVAILABLE"

_DEFAULT_TIMEOUT = 300


def _resolve_docker_command() -> str | None:
    """Return the configured or auto-discovered Docker binary, or None."""
    return os.getenv("PATCHFORGE_DOCKER_COMMAND") or shlex.split(
        os.getenv("PATCHFORGE_DOCKER_COMMAND", "")
    )[0] if os.getenv("PATCHFORGE_DOCKER_COMMAND") else (
        os.getenv("PATCHFORGE_DOCKER_COMMAND") or __import__("shutil").which("docker")
    )


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

    # ------------------------------------------------------------------
    # SelectedTestExecutor protocol
    # ------------------------------------------------------------------

    def run_selected(
        self,
        workspace: OwnedWorkspace,
        selectors: list[str],
        timeout_seconds: int,
    ) -> TestObservation:
        """Execute *selectors* inside the Docker harness; return a TestObservation.

        Returns a ``timed_out`` or ``exit_code=-1`` observation (never raises)
        when Docker is unavailable, the image is unknown, or the process
        fails in a non-test way. The output field always contains the
        evidence needed by AssertionFailureExtractor.
        """
        if not selectors:
            return TestObservation(
                phase="F2P",
                selectors=(),
                output=SELECTED_TEST_UNAVAILABLE + ": no selectors supplied",
                exit_code=-1,
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
            )

        image = self.image_tag or os.getenv("PATCHFORGE_DOCKER_IMAGE", "").strip()
        if not image:
            return TestObservation(
                phase="F2P",
                selectors=tuple(selectors),
                output=SELECTED_TEST_UNAVAILABLE
                + ": no Docker image configured (set PATCHFORGE_DOCKER_IMAGE)",
                exit_code=-1,
            )

        effective_timeout = (
            self.timeout_seconds
            if self.timeout_seconds is not None
            else int(os.getenv("SWEBENCH_HARNESS_TIMEOUT", str(_DEFAULT_TIMEOUT)))
        )

        # Build the docker run command:
        #   docker run --rm
        #     -v <workspace_root>:/testbed:ro
        #     [extra_docker_args]
        #     <image>
        #     python -m pytest <selectors> -q --tb=short
        workspace_root = str(workspace.root)
        cmd: list[str] = [
            docker_bin,
            "run",
            "--rm",
            "-v",
            f"{workspace_root}:/testbed:ro",
            *self.extra_docker_args,
            image,
            "python",
            "-m",
            "pytest",
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
            )
        except OSError as exc:
            return TestObservation(
                phase="F2P",
                selectors=tuple(selectors),
                output=f"OSError launching Docker: {exc}",
                exit_code=-1,
            )

        output = (proc.stdout + "\n" + proc.stderr)[-4000:].strip()
        passed, failed = _parse_pytest_outcomes(output, selectors)

        # Validate: if none of the requested selectors appear in any outcome,
        # the harness likely could not collect them.
        all_known = set(passed) | set(failed)
        missing = [s for s in selectors if not any(s in k for k in all_known)]
        if missing and proc.returncode not in (0, 1):
            # Non-zero exit that is NOT a test failure exit code — treat as
            # SELECTED_TEST_UNAVAILABLE so the caller can log and continue.
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
            )

        return TestObservation(
            phase="F2P",
            selectors=tuple(selectors),
            passed=tuple(passed),
            failed=tuple(failed),
            output=output,
            exit_code=proc.returncode,
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_pytest_outcomes(
    output: str, selectors: list[str]
) -> tuple[list[str], list[str]]:
    """Parse pytest -q output into (passed, failed) test-id lists.

    Falls back to selector-level pass/fail based on exit code heuristics.
    """
    passed: list[str] = []
    failed: list[str] = []
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("PASSED "):
            passed.append(line[7:].strip())
        elif line.startswith("FAILED ") or line.startswith("ERROR "):
            prefix_len = 7 if line.startswith("FAILED ") else 6
            failed.append(line[prefix_len:].strip())
    return passed, failed
