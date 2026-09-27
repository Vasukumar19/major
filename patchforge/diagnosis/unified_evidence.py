"""Unified Evidence Engine: structured relational substrate connecting static, behavioral, runtime, temporal, and probe evidence to competing hypotheses."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from patchforge.diagnosis.behavior import IssueBehaviorMap
from patchforge.diagnosis.candidate_analysis import CandidateProfile
from patchforge.diagnosis.evidence import DeepStateFlowInfo, StructuredFailureEvidence
from patchforge.repository_intelligence.graph import RepositoryGraph
from patchforge.repository_intelligence.temporal_graph import SymbolTemporalProfile, TemporalRepositoryGraph

logger = logging.getLogger(__name__)


@dataclass
class UnifiedEvidenceItem:
    """An atomic, provenance-backed factual claim with bidirectional links to hypotheses."""
    id: str
    source: str  # AST, CODE_GRAPH, TEMPORAL, TRACEBACK, STATE_FLOW, PROBE, MEMORY
    target: str  # file:symbol or file
    claim: str
    confidence: float = 1.0
    provenance: str = ""
    supporting_data: Dict[str, Any] = field(default_factory=dict)
    supports_hypotheses: List[str] = field(default_factory=list)
    contradicts_hypotheses: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "source": self.source,
            "target": self.target,
            "claim": self.claim,
            "confidence": self.confidence,
            "provenance": self.provenance,
            "supporting_data": self.supporting_data,
            "supports_hypotheses": self.supports_hypotheses,
            "contradicts_hypotheses": self.contradicts_hypotheses,
        }

    def format_line(self) -> str:
        sup = f" [Supports: {', '.join(self.supports_hypotheses)}]" if self.supports_hypotheses else ""
        con = f" [Contradicts: {', '.join(self.contradicts_hypotheses)}]" if self.contradicts_hypotheses else ""
        return f"[{self.id} | {self.source}] {self.claim} (conf={self.confidence:.2f}){sup}{con}"


class UnifiedEvidenceEngine:
    """Coordinates deterministic evidence aggregation across all repository and runtime dimensions."""

    def __init__(
        self,
        code_graph: Optional[RepositoryGraph] = None,
        temporal_graph: Optional[TemporalRepositoryGraph] = None,
    ):
        self.code_graph = code_graph
        self.temporal_graph = temporal_graph
        self.items: List[UnifiedEvidenceItem] = []
        self._next_id = 1

    def add_evidence(
        self,
        source: str,
        target: str,
        claim: str,
        confidence: float = 1.0,
        provenance: str = "",
        supporting_data: Optional[Dict[str, Any]] = None,
        supports: Optional[List[str]] = None,
        contradicts: Optional[List[str]] = None,
    ) -> UnifiedEvidenceItem:
        item = UnifiedEvidenceItem(
            id=f"E{self._next_id}",
            source=source,
            target=target,
            claim=claim,
            confidence=max(0.0, min(1.0, confidence)),
            provenance=provenance,
            supporting_data=supporting_data or {},
            supports_hypotheses=supports or [],
            contradicts_hypotheses=contradicts or [],
        )
        self._next_id += 1
        self.items.append(item)
        return item

    def aggregate_candidate_evidence(
        self,
        profile: CandidateProfile,
        behavior_map: Optional[IssueBehaviorMap] = None,
        temporal_profile: Optional[SymbolTemporalProfile] = None,
        failure_evidence: Optional[StructuredFailureEvidence] = None,
        state_flow: Optional[DeepStateFlowInfo] = None,
    ):
        """Builds structured evidence for a repair candidate."""
        target_key = f"{profile.file_path}:{profile.symbol}"

        # 1. Structural Graph Blast Radius Evidence
        br = profile.blast_radius
        classification_val = br.classification.value if hasattr(br.classification, "value") else str(br.classification)
        if classification_val == "GLOBAL_INFRASTRUCTURE":
            self.add_evidence(
                source="CODE_GRAPH",
                target=target_key,
                claim=f"`{profile.symbol}` is GLOBAL_INFRASTRUCTURE with {br.direct_callers_count} callers ({br.cross_module_callers_count} cross-module). Mutating it risks high regression.",
                confidence=0.95,
                provenance="RepositoryGraph",
                supporting_data={"callers": br.direct_callers_count},
            )
        elif classification_val == "FEATURE_LOCAL":
            self.add_evidence(
                source="CODE_GRAPH",
                target=target_key,
                claim=f"`{profile.symbol}` is FEATURE_LOCAL with bounded callers ({br.direct_callers_count}). Safe for targeted repair.",
                confidence=0.90,
                provenance="RepositoryGraph",
            )

        # 2. Traceback reachability
        if failure_evidence and failure_evidence.failing_test:
            if profile.symbol.lower() in failure_evidence.call_stack or any(profile.symbol.lower() in s.lower() for s in failure_evidence.call_stack):
                self.add_evidence(
                    source="TRACEBACK",
                    target=target_key,
                    claim=f"`{profile.symbol}` appears directly in failing execution call stack from `{failure_evidence.failing_test}`.",
                    confidence=1.0,
                    provenance=f"{failure_evidence.failing_file}:{failure_evidence.failing_line}",
                )

        # 3. Temporal Churn & Bug History
        if temporal_profile:
            if temporal_profile.bug_fix_commits > 0:
                self.add_evidence(
                    source="TEMPORAL",
                    target=target_key,
                    claim=f"`{profile.symbol}` has a history of {temporal_profile.bug_fix_commits} bug fixes (churn score: {temporal_profile.churn_score:.2f}).",
                    confidence=0.85,
                    provenance="GitLog",
                    supporting_data={"bug_fixes": temporal_profile.bug_fix_commits},
                )

        # 4. State Flow Mutations
        if state_flow:
            if state_flow.overwritten_vars:
                self.add_evidence(
                    source="STATE_FLOW",
                    target=target_key,
                    claim=f"`{profile.symbol}` overwrites state variables: {', '.join(state_flow.overwritten_vars)}.",
                    confidence=0.85,
                    provenance="ControlFlowBuilder",
                    supporting_data={"overwritten": state_flow.overwritten_vars},
                )
            if state_flow.mutated_attributes:
                self.add_evidence(
                    source="STATE_FLOW",
                    target=target_key,
                    claim=f"`{profile.symbol}` mutates attributes/containers: {', '.join(state_flow.mutated_attributes)}.",
                    confidence=0.85,
                    provenance="ControlFlowBuilder",
                    supporting_data={"mutated": state_flow.mutated_attributes},
                )

    def evaluate_hypothesis_support(self, hypothesis_id: str) -> Tuple[float, List[UnifiedEvidenceItem], List[UnifiedEvidenceItem]]:
        """Calculates evidence-based support score for a hypothesis."""
        supporting = [e for e in self.items if hypothesis_id in e.supports_hypotheses]
        contradicting = [e for e in self.items if hypothesis_id in e.contradicts_hypotheses]

        score = sum(e.confidence for e in supporting) - 1.5 * sum(e.confidence for e in contradicting)
        return round(score, 3), supporting, contradicting

    def format_summary_for_prompt(self, max_items: int = 12) -> str:
        if not self.items:
            return "No structured unified evidence recorded."
        lines = ["### STRUCTURED UNIFIED EVIDENCE SUBSTRATE:"]
        for item in self.items[:max_items]:
            lines.append("  * " + item.format_line())
        return "\n".join(lines)
