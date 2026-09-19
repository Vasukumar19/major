"""A competing root-cause hypothesis, traceable to evidence."""
from __future__ import annotations

from dataclasses import dataclass, field

from patchforge.retrieval.evidence import Evidence


@dataclass
class Hypothesis:
    id: str = ""
    description: str = ""
    supporting_evidence: list[Evidence] = field(default_factory=list)
    contradicting_evidence: list[Evidence] = field(default_factory=list)
    affected_files: list[str] = field(default_factory=list)
    affected_symbols: list[str] = field(default_factory=list)
    confidence: float = 0.0
    expected_behavior: str = ""
    rank_score: float = 0.0
    rank_breakdown: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "description": self.description,
            "supporting_evidence": [e.to_dict() for e in self.supporting_evidence],
            "contradicting_evidence": [e.to_dict() for e in self.contradicting_evidence],
            "affected_files": self.affected_files,
            "affected_symbols": self.affected_symbols,
            "confidence": self.confidence,
            "expected_behavior": self.expected_behavior,
            "rank_score": self.rank_score,
            "rank_breakdown": self.rank_breakdown,
        }


@dataclass
class RefinedHypothesis:
    """Hypothesis refined from concrete execution evidence and diagnosis."""
    original_hypothesis: str = ""
    execution_evidence_summary: str = ""
    refined_hypothesis: str = ""
    repair_plan: list[str] = field(default_factory=list)
    repair_constraints: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "original_hypothesis": self.original_hypothesis,
            "execution_evidence_summary": self.execution_evidence_summary,
            "refined_hypothesis": self.refined_hypothesis,
            "repair_plan": list(self.repair_plan),
            "repair_constraints": list(self.repair_constraints),
        }
