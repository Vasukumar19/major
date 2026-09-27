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

        # Probe 2: State observation if keywords present
        probe_var = ""
        for word in ["proxies", "kwargs", "config", "settings", "headers", "args", "params", "options", "state"]:
            if word in h1.cause.lower() or word in h2.cause.lower():
                probe_var = word
                break

        if probe_var:
            probes.append(
                ProbeSpecification(
                    probe_id=f"P_{probe_var.upper()}",
                    probe_type=ProbeType.VARIABLE_VALUE,
                    file_path=target_file,
                    symbol=target_symbol,
                    target_line=target_line + 2,
                    variable_name=probe_var,
                    expected_if_h1_true="not empty",
                    expected_if_h2_true="None or empty",
                )
            )

        return probes[:2]  # strictly bounded to max 2 probes
