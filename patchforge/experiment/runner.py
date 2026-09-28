"""Experiment Runner: executes bounded sandboxed dynamic probes inside Docker or local environment."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, List, Optional

from patchforge.diagnosis.diagnosis import CompetingDiagnosisResult
from patchforge.experiment.counterfactual import CounterfactualEngine
from patchforge.experiment.injector import ProbeInjector
from patchforge.experiment.observer import ProbeObserver
from patchforge.experiment.planner import ExperimentPlanner
from patchforge.experiment.probe import ExperimentResult, ProbeSpecification
from patchforge.verification.tester import Tester

logger = logging.getLogger(__name__)


class ExperimentRunner:
    """Coordinates probe planning, temporary instrumentation, test execution, observation parsing, and rollback."""

    def __init__(self, repo_dir: str, tester: Optional[Tester] = None):
        self.repo_dir = repo_dir
        self.tester = tester
        self.injector = ProbeInjector(repo_dir)

    def run_experiment(
        self,
        instance_id: str,
        diagnosis: CompetingDiagnosisResult,
        target_file: str,
        target_symbol: str,
        target_line: int,
    ) -> ExperimentResult:
        """Executes a bounded dynamic experiment with guaranteed rollback."""
        probes = ExperimentPlanner.plan_experiment(
            diagnosis=diagnosis,
            target_file=target_file,
            target_symbol=target_symbol,
            target_line=target_line,
        )

        if not probes or not self.tester:
            return ExperimentResult(
                experiment_id="EXP_SKIPPED",
                question="No distinguishing probes planned or no tester available.",
                summary="Experiment skipped: static evidence sufficient or tester unavailable.",
            )

        try:
            # 1. Inject temporary probes
            self.injector.inject_probes(probes)

            # 2. Run targeted test via tester
            # Tester applies empty patch or current git diff to observe baseline failing test
            verdict = self.tester.run(instance_id, patch_text="")
            raw_output = getattr(verdict, "output", "") or getattr(verdict, "error", "") or str(verdict)

            # 3. Parse sentinel observations
            observations = ProbeObserver.parse_output(raw_output)

            # 4. Evaluate counterfactuals and update hypotheses
            result = CounterfactualEngine.evaluate_observations(probes, observations, diagnosis)
            return result

        except Exception as e:
            logger.warning(f"Experiment execution encountered error: {e}")
            return ExperimentResult(
                experiment_id="EXP_ERROR",
                question=str(e),
                summary=f"Experiment aborted due to error: {e}",
            )
        finally:
            # Guaranteed clean rollback
            self.injector.restore_all()
