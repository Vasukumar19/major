"""Diagnostic validation probes for testable hypotheses in PatchForge v0.3."""
from __future__ import annotations

import abc
import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from patchforge.reasoning.hypothesis import Hypothesis
from patchforge.retrieval.evidence import Evidence, EvidenceType


@dataclass
class ProbeResult:
    """Result of running a diagnostic probe against a hypothesis."""
    hypothesis_id: str
    probe_type: str  # "STATIC", "TEST", "REPRODUCTION", "TRACEBACK"
    prediction: str
    observation: str
    supports: bool
    contradicts: bool
    evidence: list[Evidence] = field(default_factory=list)
    cost: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "hypothesis_id": self.hypothesis_id,
            "probe_type": self.probe_type,
            "prediction": self.prediction,
            "observation": self.observation,
            "supports": self.supports,
            "contradicts": self.contradicts,
            "evidence": [e.to_dict() if hasattr(e, "to_dict") else str(e) for e in self.evidence],
            "cost": self.cost,
        }


class Probe(abc.ABC):
    """Abstract base class for diagnostic validation probes."""

    name: str = ""
    probe_type: str = ""

    @abc.abstractmethod
    def run(self, hypothesis: Hypothesis, context: dict[str, Any]) -> ProbeResult:
        """Execute probe and evaluate if observation supports or contradicts the hypothesis."""
        raise NotImplementedError


class StaticProbe(Probe):
    """Inspects code syntax and AST nodes to verify hypothesized structural defects."""

    name = "static_probe"
    probe_type = "STATIC"

    def run(self, hypothesis: Hypothesis, context: dict[str, Any]) -> ProbeResult:
        repo_dir = context.get("repo_dir", ".")
        repo_path = Path(repo_dir)

        prediction = f"Hypothesized symbol or pattern exists in predicted files."
        observations = []
        supports = False
        contradicts = False
        ev_list = []

        for e in hypothesis.supporting_evidence:
            if not e.file:
                continue
            full_path = repo_path / e.file
            if not full_path.exists():
                contradicts = True
                observations.append(f"File {e.file} does not exist in repository.")
                continue

            try:
                with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
                if e.symbol and e.symbol in content:
                    supports = True
                    observations.append(f"Found symbol '{e.symbol}' in {e.file}.")
                    ev_list.append(Evidence(
                        source="static_probe",
                        type=EvidenceType.STATIC_REFERENCE.value,
                        file=e.file,
                        symbol=e.symbol,
                        relevance=0.9,
                        explanation=f"Static probe verified {e.symbol} in {e.file}",
                    ))
                elif not e.symbol:
                    supports = True
                    observations.append(f"Verified {e.file} presence.")
            except Exception as ex:
                observations.append(f"Error reading {e.file}: {str(ex)}")

        if not observations:
            observation = "No file evidence to probe statically."
        else:
            observation = "; ".join(observations)

        return ProbeResult(
            hypothesis_id=hypothesis.id,
            probe_type=self.probe_type,
            prediction=prediction,
            observation=observation,
            supports=supports and not contradicts,
            contradicts=contradicts,
            evidence=ev_list,
        )


class TestProbe(Probe):
    """Checks existing test cases and expected assertions against hypothesis."""

    name = "test_probe"
    probe_type = "TEST"

    def run(self, hypothesis: Hypothesis, context: dict[str, Any]) -> ProbeResult:
        failing_tests = context.get("failing_tests", [])
        prediction = f"Hypothesis explains failure in tests: {', '.join(failing_tests[:3])}"
        
        # Check if any supporting evidence mentions test files or failing assertions
        ev_mentions_test = any("test" in (e.file or "").lower() or "assert" in e.explanation.lower() for e in hypothesis.supporting_evidence)
        supports = ev_mentions_test or len(failing_tests) > 0
        observation = f"Hypothesis links to {len(failing_tests)} failing test targets." if supports else "No direct link to failing test assertions found."

        return ProbeResult(
            hypothesis_id=hypothesis.id,
            probe_type=self.probe_type,
            prediction=prediction,
            observation=observation,
            supports=supports,
            contradicts=False,
        )


class ReproductionProbe(Probe):
    """Validates if hypothesis aligns with minimal reproduction evidence."""

    name = "reproduction_probe"
    probe_type = "REPRODUCTION"

    def run(self, hypothesis: Hypothesis, context: dict[str, Any]) -> ProbeResult:
        repro_output = context.get("repro_output", "")
        prediction = "Hypothesis directly addresses the runtime error message."
        
        supports = False
        contradicts = False
        if repro_output and hypothesis.description:
            # Check for keyword overlap
            keywords = [w.lower() for w in repro_output.split() if len(w) > 4]
            overlap = [k for k in keywords if k in hypothesis.description.lower()]
            if overlap:
                supports = True
                observation = f"Observed keyword overlap with reproduction: {', '.join(overlap[:5])}"
            else:
                observation = "No direct keyword overlap with reproduction output."
        else:
            observation = "No reproduction output available for probing."

        return ProbeResult(
            hypothesis_id=hypothesis.id,
            probe_type=self.probe_type,
            prediction=prediction,
            observation=observation,
            supports=supports,
            contradicts=contradicts,
        )


class TracebackProbe(Probe):
    """Validates if hypothesis targets files and line numbers present in stack traces."""

    name = "traceback_probe"
    probe_type = "TRACEBACK"

    def run(self, hypothesis: Hypothesis, context: dict[str, Any]) -> ProbeResult:
        traceback = context.get("traceback", "")
        prediction = "Hypothesis targets a file/function present in the traceback."
        
        if not traceback:
            return ProbeResult(
                hypothesis_id=hypothesis.id,
                probe_type=self.probe_type,
                prediction=prediction,
                observation="No stack trace provided in context.",
                supports=True,  # Neutral
                contradicts=False,
            )

        matched_files = []
        for e in hypothesis.supporting_evidence:
            if e.file and e.file in traceback:
                matched_files.append(e.file)

        supports = len(matched_files) > 0
        observation = f"Traceback mentions hypothesized files: {', '.join(matched_files)}" if supports else "None of hypothesized files appear in traceback."

        return ProbeResult(
            hypothesis_id=hypothesis.id,
            probe_type=self.probe_type,
            prediction=prediction,
            observation=observation,
            supports=supports,
            contradicts=not supports and len(traceback) > 50,
        )
