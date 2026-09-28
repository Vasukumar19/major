"""Experiment Planner: formulates minimal dynamic probes to distinguish competing hypotheses."""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from patchforge.diagnosis.diagnosis import CompetingDiagnosisResult, DiagnosisHypothesis
from patchforge.experiment.probe import ProbeSpecification, ProbeType

logger = logging.getLogger(__name__)


class ExperimentPlanner:
    """Decides what observations would distinguish competing hypotheses and generates minimal probes."""

    @classmethod
    def plan_experiment(
        cls,
        diagnosis: CompetingDiagnosisResult,
        target_file: str,
        target_symbol: str,
        target_line: int,
    ) -> List[ProbeSpecification]:
        """Formulates bounded runtime probes to test the primary divergence between H1 and H2."""
        if not diagnosis.hypotheses or len(diagnosis.hypotheses) < 2:
            return []

        h1 = diagnosis.hypotheses[0]
        h2 = diagnosis.hypotheses[1]

        # Probe 1: Target function entry
        probes: List[ProbeSpecification] = [
            ProbeSpecification(
                probe_id="P_ENTRY",
                probe_type=ProbeType.FUNCTION_ENTRY,
                file_path=target_file,
                symbol=target_symbol,
                target_line=target_line,
                expected_if_h1_true="ENTER",
                expected_if_h2_true="ENTER",
            )
        ]

        # Probe 2: Information-directed state observation to discriminate H1 vs H2
        probe_var = ""
        if getattr(h1, "prediction", None) and h1.prediction.discriminating_variable:
            probe_var = h1.prediction.discriminating_variable
        elif getattr(h2, "prediction", None) and h2.prediction.discriminating_variable:
            probe_var = h2.prediction.discriminating_variable

        if not probe_var:
            for word in ["proxies", "kwargs", "config", "settings", "headers", "args", "params", "options", "state", "mode", "text", "scope", "encoding"]:
                if word in h1.cause.lower() or word in h2.cause.lower():
                    probe_var = word
                    break

        if probe_var:
            exp1 = h1.prediction.expected_output_state.get(probe_var, "observed") if getattr(h1, "prediction", None) else "defined"
            exp2 = h2.prediction.expected_output_state.get(probe_var, "contradicts") if getattr(h2, "prediction", None) else "None or different"
            probes.append(
                ProbeSpecification(
                    probe_id=f"P_{probe_var.upper()}",
                    probe_type=ProbeType.VARIABLE_VALUE,
                    file_path=target_file,
                    symbol=target_symbol,
                    target_line=target_line,
                    variable_name=probe_var,
                    expected_if_h1_true=exp1,
                    expected_if_h2_true=exp2,
                )
            )

        return probes[:2]  # strictly bounded to max 2 probes
