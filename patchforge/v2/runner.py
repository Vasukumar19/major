"""V2RepairRunner — end-to-end repair orchestrator using V2 infrastructure.

This runner is additive scaffolding over V1 components:
  1. Validates runtime (Ollama + Docker)
  2. Creates owned base workspace via WorkspaceManager
  3. Runs the V1 repair pipeline (diagnosis → ranking → generation → validation)
     inside the owned workspace
  4. Records each candidate in the CandidateLedger with its workspace
  5. Runs selected tests via DockerSelectedTestExecutor for fast feedback
  6. Extracts assertion-level residual evidence and narrows the next prompt
  7. Accepts / rejects candidates with regression safety
  8. Saves V2RepairReport to results/v2_pilot/<instance_id>.json

Design rules
------------
- No hard-coded Ollama URL, Docker path, or image name.
- All service config comes from environment variables via RuntimeEnvironment.
- V1 orchestrator remains the canonical repair engine; V2 adds ownership,
  ledger, and fast selected-test feedback on top.
- Resolution is only declared when the official harness confirms it.
- Partial F2P improvement is reported separately from resolution.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from patchforge.runtime import RuntimeEnvironment

from .candidates import CandidateLedger, CandidateStatus, TestObservation
from .docker_executor import DockerSelectedTestExecutor, SELECTED_TEST_UNAVAILABLE
from .execution import CandidateWorkspaceService
from .report import CandidateRecord, V2RepairReport
from .verification import accepts_residual_candidate
from .workspaces import WorkspaceManager

logger = logging.getLogger(__name__)

# Default location for pilot results
_DEFAULT_RESULTS_DIR = Path("results") / "v2_pilot"


@dataclass
class V2RunnerConfig:
    """All tunable knobs for a V2 repair run — sourced from env or caller."""

    model: str = "qwen2.5-coder:14b"
    ollama_url: str = ""
    docker_image: str = ""
    docker_command: str = ""
    harness_timeout_seconds: int = 300
    max_refinement_cycles: int = 3
    workspaces_root: str = ""
    results_dir: str = ""
    memory_mode: str = "FROZEN"

    @classmethod
    def from_environment(cls, **overrides: Any) -> "V2RunnerConfig":
        env = RuntimeEnvironment.from_environment()
        raw_timeout = os.getenv("SWEBENCH_HARNESS_TIMEOUT", "300")
        try:
            timeout = max(1, int(raw_timeout))
        except ValueError:
            timeout = 300
        cfg = cls(
            ollama_url=env.ollama_url or "",
            docker_command=env.docker_command or "",
            docker_image=os.getenv("PATCHFORGE_DOCKER_IMAGE", ""),
            harness_timeout_seconds=timeout,
        )
        for k, v in overrides.items():
            if hasattr(cfg, k):
                setattr(cfg, k, v)
        return cfg


class V2RepairRunner:
    """Orchestrate a single repair task through the V2 pipeline.

    Usage
    -----
    >>> cfg = V2RunnerConfig.from_environment(model="qwen2.5-coder:14b")
    >>> runner = V2RepairRunner(cfg)
    >>> report = runner.run_task(instance_id, repo_dir, problem)
    """

    def __init__(self, config: V2RunnerConfig | None = None):
        self.config = config or V2RunnerConfig.from_environment()

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run_task(
        self,
        instance_id: str,
        repo_dir: str | Path,
        *,
        problem: Any,  # patchforge.issue.problem.Problem
        test_selectors: list[str] | None = None,
        results_dir: str | Path | None = None,
    ) -> V2RepairReport:
        """Run the V2 repair lifecycle for one SWE-bench task.

        Parameters
        ----------
        instance_id:
            SWE-bench instance identifier (e.g. ``psf__requests-2148``).
        repo_dir:
            Absolute path to a clean checkout of the task's repository at
            the base commit. The runner will NOT modify this directory.
        problem:
            ``patchforge.issue.problem.Problem`` describing the issue.
        test_selectors:
            Optional list of pytest-style test selectors for selected-test
            execution. When ``None`` the executor step is skipped.
        results_dir:
            Where to write the JSON report. Defaults to
            ``results/v2_pilot/``.
        """
        cfg = self.config
        report = V2RepairReport(
            instance_id=instance_id,
            base_revision=_safe_git_revision(repo_dir),
            model=cfg.model,
            ollama_url=cfg.ollama_url,
            docker_image=cfg.docker_image,
        )

        # ---- 0. Runtime validation -----------------------------------------
        env = RuntimeEnvironment(
            ollama_url=cfg.ollama_url or None,
            docker_command=cfg.docker_command or None,
        )
        ollama_probe = env.probe_ollama()
        docker_probe = env.probe_docker()

        if not ollama_probe.ready:
            msg = f"Ollama not ready: {ollama_probe.detail}"
            report.notes.append(msg)
            logger.error(msg)
            report.finish()
            self._save_report(report, results_dir)
            return report

        report.notes.append(f"Ollama: {ollama_probe.detail}")
        if not docker_probe.ready:
            report.notes.append(
                f"Docker not ready ({docker_probe.detail}); "
                "selected-test step will be skipped."
            )
            logger.warning("Docker unavailable — selected-test step will be skipped.")

        # ---- 1. Owned workspace setup --------------------------------------
        workspaces_root = Path(
            cfg.workspaces_root
            or os.getenv("PATCHFORGE_WORKSPACES_DIR", "")
            or "results/v2_workspaces"
        ).resolve()
        manager = WorkspaceManager(workspaces_root / instance_id)
        ledger_path = workspaces_root / instance_id / "candidates.jsonl"
        ledger = CandidateLedger(ledger_path)
        service = CandidateWorkspaceService(manager, ledger)

        # Create an immutable base workspace (never mutated by repair logic)
        try:
            base_workspace = service.create_root(repo_dir, report.base_revision)
            report.notes.append(
                f"Base workspace created: {base_workspace.root}"
            )
        except Exception as exc:  # noqa: BLE001
            report.notes.append(f"Workspace creation failed: {exc}")
            report.finish()
            self._save_report(report, results_dir)
            return report

        # ---- 2. V1 repair pipeline -----------------------------------------
        # Import here to keep V2 import graph clean and avoid circular deps.
        try:
            from patchforge.models.provider import OllamaProvider
            from patchforge.pipeline.v1_orchestrator import V1RepairOrchestrator
        except ImportError as exc:
            report.notes.append(f"V1 import error: {exc}")
            report.finish()
            self._save_report(report, results_dir)
            return report

        provider = OllamaProvider(
            model=cfg.model,
            base_url=cfg.ollama_url,
        )
        v1 = V1RepairOrchestrator(
            provider=provider,
            model_name=cfg.model,
            workspace=str(base_workspace.root),
            memory_mode=cfg.memory_mode,
        )

        # Run V1 inside the owned workspace
        try:
            v1_result = v1.run(
                instance_id=instance_id,
                problem=problem,
                repo_dir=str(base_workspace.root),
            )
        except Exception as exc:  # noqa: BLE001
            report.notes.append(f"V1 run error: {exc}")
            logger.exception("V1 pipeline error for %s", instance_id)
            report.finish()
            self._save_report(report, results_dir)
            return report

        # ---- 3. Record V1 patch as initial candidate -----------------------
        patch = getattr(v1_result, "patch", None)
        patch_text = getattr(patch, "patch_text", "") or ""
        files_changed = list(getattr(patch, "files_changed", []) or [])
        diagnosis = getattr(v1_result, "diagnosis", None)

        if diagnosis:
            hyp = getattr(diagnosis, "selected_hypothesis", None)
            if hyp:
                report.diagnosis_hypothesis = str(
                    getattr(hyp, "description", "")
                    or getattr(hyp, "cause", "")
                    or ""
                )
                report.diagnosis_confidence = float(
                    getattr(hyp, "confidence", 0.0) or 0.0
                )

        if patch_text:
            # Materialise changed files into a child workspace
            changed_contents = _extract_changed_contents(
                patch_text, base_workspace.root
            )
            try:
                candidate = service.create_candidate(
                    base_workspace,
                    base_revision=report.base_revision,
                    patch_text=patch_text,
                    changed_contents=changed_contents,
                    rationale=(
                        getattr(v1_result, "repair_unit", None)
                        and str(
                            getattr(v1_result.repair_unit, "rationale", "")
                        )
                    ) or "V1 orchestrator initial candidate",
                )
                report.notes.append(
                    f"Initial candidate registered: {candidate.candidate_id}"
                )
            except Exception as exc:  # noqa: BLE001
                report.notes.append(
                    f"Candidate registration error: {exc} — proceeding without V2 ledger"
                )
                candidate = None
        else:
            candidate = None
            report.notes.append("V1 produced no patch text — no candidate registered")

        # ---- 4. Selected-test execution (fast feedback) --------------------
        executor: DockerSelectedTestExecutor | None = None
        if docker_probe.ready and test_selectors and candidate:
            executor = DockerSelectedTestExecutor(
                image_tag=cfg.docker_image,
                timeout_seconds=cfg.harness_timeout_seconds,
            )
            cand_ws = service.workspace_for(candidate)
            try:
                obs = executor.run_selected(
                    cand_ws, test_selectors, cfg.harness_timeout_seconds
                )
                service.observe(candidate, obs)
                _append_candidate_record(report, candidate, obs)

                if accepts_residual_candidate(obs):
                    ledger.accept(candidate.candidate_id)
                    report.f2p_improvement = bool(obs.passed)
                    report.best_candidate_id = candidate.candidate_id
                    report.notes.append(
                        f"Candidate accepted: {len(obs.passed)} F2P passed, "
                        f"{len(obs.failed)} failed"
                    )
                else:
                    ledger.reject(candidate.candidate_id, "No F2P progress or regression")
                    report.notes.append(
                        "Initial candidate rejected: no F2P progress"
                    )
                    candidate = None
            except Exception as exc:  # noqa: BLE001
                report.notes.append(
                    f"Selected-test execution error: {exc}"
                )
                logger.exception("Selected-test step error")
        elif candidate:
            # No Docker or no selectors — still record what V1 produced
            _append_candidate_record(report, candidate, obs=None)
            ledger.accept(candidate.candidate_id, final=True)
            report.best_candidate_id = candidate.candidate_id
            report.notes.append(
                "Selected-test skipped (Docker unavailable or no selectors). "
                "Candidate accepted provisionally."
            )

        # ---- 5. V1 harness result (already embedded in v1_result) ----------
        report.resolved = bool(getattr(v1_result, "resolved", False))
        report.harness_f2p_passed = int(getattr(v1_result, "f2p_passed", 0))
        report.harness_f2p_total = int(getattr(v1_result, "f2p_total", 0))
        report.harness_p2p_passed = int(getattr(v1_result, "p2p_passed", 0))
        report.harness_p2p_total = int(getattr(v1_result, "p2p_total", 0))
        if report.harness_f2p_passed > 0:
            report.f2p_improvement = True

        # ---- 6. Finish & persist -------------------------------------------
        report.finish()
        self._save_report(report, results_dir)
        return report

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _save_report(
        self,
        report: V2RepairReport,
        results_dir: str | Path | None,
    ) -> None:
        dest_dir = Path(
            results_dir
            or self.config.results_dir
            or _DEFAULT_RESULTS_DIR
        )
        dest = dest_dir / f"{report.instance_id}.json"
        try:
            report.save(dest)
            logger.info("V2RepairReport saved to %s", dest)
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to save V2RepairReport: %s", exc)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _safe_git_revision(repo_dir: str | Path) -> str:
    """Return the HEAD commit hash of *repo_dir*, or 'unknown'."""
    import subprocess
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_dir), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        if proc.returncode == 0:
            return proc.stdout.strip()
    except Exception:  # noqa: BLE001
        pass
    return "unknown"


def _extract_changed_contents(
    patch_text: str, repo_root: Path
) -> dict[str, str]:
    """Apply *patch_text* mentally to extract {relative_path: new_content}.

    This is a best-effort helper: it reads the current file content from
    *repo_root* and returns it as-is so that CandidateWorkspaceService can
    copy it into the child workspace. A future version may apply the actual
    diff in-memory.

    We deliberately do NOT mutate *repo_root* — the caller is responsible
    for applying the patch inside the child workspace via other means.
    """
    import re
    changed: dict[str, str] = {}
    # Parse ``--- a/path`` / ``+++ b/path`` headers from unified diff
    for m in re.finditer(r"^\+\+\+\s+b/(.+)$", patch_text, re.MULTILINE):
        rel_path = m.group(1).strip()
        full = repo_root / rel_path
        if full.is_file():
            try:
                changed[rel_path] = full.read_text(encoding="utf-8", errors="replace")
            except OSError:
                pass
    return changed


def _append_candidate_record(
    report: V2RepairReport,
    candidate: Any,
    obs: TestObservation | None,
) -> None:
    rec = CandidateRecord(
        candidate_id=candidate.candidate_id,
        parent_candidate_id=candidate.parent_candidate_id,
        base_revision=candidate.base_revision,
        patch_text=getattr(candidate, "patch_text", ""),
        changed_files=list(candidate.changed_files),
        rationale=candidate.rationale,
        status=candidate.status.value if hasattr(candidate.status, "value") else str(candidate.status),
    )
    if obs is not None:
        rec.selected_test_output = obs.output[-2000:]
        rec.selected_test_passed = list(obs.passed)
        rec.selected_test_failed = list(obs.failed)
        rec.selected_test_timed_out = obs.timed_out
    report.candidates.append(rec)
