"""Executable Counterfactual Analysis: compares observed dynamic probe state against hypothesis predictions."""
from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

from patchforge.diagnosis.diagnosis import CompetingDiagnosisResult, DiagnosisHypothesis
from patchforge.experiment.probe import ExperimentResult, ProbeObservation, ProbeSpecification

logger = logging.getLogger(__name__)


class CounterfactualEngine:
    """Evaluates whether dynamic runtime observations support or contradict hypothesis counterfactuals."""

    @classmethod
    def evaluate_observations(
        cls,
        probes: List[ProbeSpecification],
        observations: List[ProbeObservation],
        diagnosis: CompetingDiagnosisResult,
    ) -> ExperimentResult:
        """Evaluates observations against expected values and updates hypothesis confidence scores."""
        obs_map = {o.probe_id: o for o in observations}
        supported_h: Optional[str] = None
        contradicted: List[str] = []
        conf_delta = 0.0

        for probe in probes:
            obs = obs_map.get(probe.probe_id)
            if not obs:
                continue

            # Check Function Entry hit
            if probe.probe_id == "P_ENTRY" and obs.observed_value == "ENTER":
                # Confirm target execution reached
                conf_delta += 0.1
                if diagnosis.hypotheses:
                    diagnosis.hypotheses[0].confidence = min(1.0, diagnosis.hypotheses[0].confidence + 0.1)

            # Check variable observation
            if probe.variable_name and obs.observed_value:
                val_repr = obs.observed_value
                if val_repr not in ("None", "{}", "[]", "''", "ERR:"):
                    # Variable exists and is populated
                    supported_h = "A"
                    conf_delta += 0.2
                    if len(diagnosis.hypotheses) > 1:
                        contradicted.append(diagnosis.hypotheses[1].id)
                        diagnosis.hypotheses[1].confidence = max(0.1, diagnosis.hypotheses[1].confidence - 0.25)
                else:
                    supported_h = "B"
                    conf_delta += 0.2
                    if diagnosis.hypotheses:
                        diagnosis.hypotheses[0].confidence = max(0.1, diagnosis.hypotheses[0].confidence - 0.25)

        summary = f"Experiment tested {len(probes)} probes. Observations: {len(observations)} captured."
        if supported_h:
            summary += f" Dynamic evidence supports Hypothesis {supported_h}."

        return ExperimentResult(
            experiment_id="EXP_1",
            question="Does execution reach target and what is the runtime state?",
            probes=probes,
            observations=observations,
            supported_hypothesis=supported_h,
            contradicted_hypotheses=contradicted,
            confidence_delta=conf_delta,
            summary=summary,
        )
