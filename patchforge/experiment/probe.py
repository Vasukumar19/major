"""Probe specifications and observation models for dynamic sandboxed experimentation."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class ProbeType(str, Enum):
    VARIABLE_VALUE = "VARIABLE_VALUE"
    RETURN_VALUE = "RETURN_VALUE"
    FUNCTION_ENTRY = "FUNCTION_ENTRY"
    FUNCTION_EXIT = "FUNCTION_EXIT"
    BRANCH_DECISION = "BRANCH_DECISION"
    STATE_MUTATION = "STATE_MUTATION"
    EXCEPTION = "EXCEPTION"
    CALL_SEQUENCE = "CALL_SEQUENCE"


@dataclass
class ProbeSpecification:
    """A minimal, non-destructive runtime probe specification."""
    probe_id: str
    probe_type: ProbeType
    file_path: str
    symbol: str
    target_line: int
    variable_name: str = ""
    expression: str = ""
    expected_if_h1_true: str = ""
    expected_if_h2_true: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "probe_id": self.probe_id,
            "probe_type": self.probe_type.value,
            "file_path": self.file_path,
            "symbol": self.symbol,
            "target_line": self.target_line,
            "variable_name": self.variable_name,
            "expression": self.expression,
            "expected_if_h1_true": self.expected_if_h1_true,
            "expected_if_h2_true": self.expected_if_h2_true,
        }


@dataclass
class ProbeObservation:
    """Runtime observation captured from sandbox execution."""
    probe_id: str
    observed_value: str
    raw_output: str = ""
    hit_count: int = 1
    exception_caught: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "probe_id": self.probe_id,
            "observed_value": self.observed_value,
            "raw_output": self.raw_output,
            "hit_count": self.hit_count,
            "exception_caught": self.exception_caught,
        }


@dataclass
class ExperimentResult:
    """The outcome of a dynamic experiment distinguishing competing hypotheses."""
    experiment_id: str
    question: str
    probes: List[ProbeSpecification] = field(default_factory=list)
    observations: List[ProbeObservation] = field(default_factory=list)
    supported_hypothesis: Optional[str] = None
    contradicted_hypotheses: List[str] = field(default_factory=list)
    confidence_delta: float = 0.0
    summary: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "question": self.question,
            "probes": [p.to_dict() for p in self.probes],
            "observations": [o.to_dict() for o in self.observations],
            "supported_hypothesis": self.supported_hypothesis,
            "contradicted_hypotheses": self.contradicted_hypotheses,
            "confidence_delta": self.confidence_delta,
            "summary": self.summary,
        }
