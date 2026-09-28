"""Competing behavioral diagnosis engine with deterministic hypothesis elimination."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from patchforge.diagnosis.behavior import IssueBehaviorMap
from patchforge.diagnosis.candidate_analysis import CandidateProfile, HelperClassification
from patchforge.diagnosis.evidence import DeepStateFlowInfo, StructuredFailureEvidence

logger = logging.getLogger(__name__)


@dataclass
class CausalPrediction:
    """Falsifiable causal prediction declared by a hypothesis."""
    expected_branch: str = ""
    expected_mutations: List[str] = field(default_factory=list)
    expected_output_state: Dict[str, str] = field(default_factory=dict)
    forbidden_events: List[str] = field(default_factory=list)
    discriminating_variable: str = ""
    support_count: int = 0
    contradiction_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "expected_branch": self.expected_branch,
            "expected_mutations": self.expected_mutations,
            "expected_output_state": self.expected_output_state,
            "forbidden_events": self.forbidden_events,
            "discriminating_variable": self.discriminating_variable,
            "support_count": self.support_count,
            "contradiction_count": self.contradiction_count,
        }


@dataclass
class DiagnosisHypothesis:
    """A competing behavioral hypothesis explaining a defect."""
    id: str = "A"
    cause: str = ""
    invariant: str = ""
    repair_strategy: str = ""
    affected_sites: List[str] = field(default_factory=list)
    supporting_evidence: List[str] = field(default_factory=list)
    contradicting_evidence: List[str] = field(default_factory=list)
    affected_tests: List[str] = field(default_factory=list)
    unexplained_symptoms: List[str] = field(default_factory=list)
    divergent_predicate: str = ""
    expected_transition: str = ""
    prediction: Optional[CausalPrediction] = None
    support_count: int = 0
    contradiction_count: int = 0
    semantic_radius: str = "FEATURE_LOCAL"
    confidence: float = 0.8
    eliminated: bool = False
    elimination_reason: str = ""
    counterfactual_score: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "cause": self.cause,
            "divergent_predicate": self.divergent_predicate,
            "expected_transition": self.expected_transition,
            "prediction": self.prediction.to_dict() if self.prediction else None,
            "support_count": self.support_count,
            "contradiction_count": self.contradiction_count,
            "invariant": self.invariant,
            "repair_strategy": self.repair_strategy,
            "affected_sites": self.affected_sites,
            "supporting_evidence": self.supporting_evidence,
            "contradicting_evidence": self.contradicting_evidence,
            "affected_tests": self.affected_tests,
            "unexplained_symptoms": self.unexplained_symptoms,
            "semantic_radius": self.semantic_radius,
            "confidence": self.confidence,
            "eliminated": self.eliminated,
            "elimination_reason": self.elimination_reason,
            "counterfactual_score": self.counterfactual_score,
        }


@dataclass
class CompetingDiagnosisResult:
    """Result of competing diagnosis containing ranked hypotheses and selected winner."""
    hypotheses: List[DiagnosisHypothesis]
    selected_hypothesis_idx: int = 0
    selected_hypothesis: Optional[DiagnosisHypothesis] = None
    repair_sites: List[str] = field(default_factory=list)
    raw_response: str = ""
    defect_confidence: float = 0.95
    classification: str = "confirmed_code_defect"

    def __post_init__(self):
        if self.hypotheses and (self.selected_hypothesis is None or self.selected_hypothesis_idx < len(self.hypotheses)):
            self.selected_hypothesis = self.hypotheses[self.selected_hypothesis_idx]

    # --- Backward compatibility with DiagnosisResult ---
    @property
    def cause(self) -> str:
        return self.selected_hypothesis.cause if self.selected_hypothesis else ""

    @property
    def invariant(self) -> str:
        return self.selected_hypothesis.invariant if self.selected_hypothesis else ""

    @property
    def repair_strategy(self) -> str:
        return self.selected_hypothesis.repair_strategy if self.selected_hypothesis else ""

    @property
    def affected_sites(self) -> List[str]:
        return self.repair_sites or (self.selected_hypothesis.affected_sites if self.selected_hypothesis else [])

    @property
    def expected_behavior(self) -> str:
        return ""

    @property
    def evidence(self) -> List[str]:
        return self.selected_hypothesis.supporting_evidence if self.selected_hypothesis else []

    @property
    def repair_justification(self) -> str:
        return ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cause": self.cause,
            "invariant": self.invariant,
            "repair_strategy": self.repair_strategy,
            "affected_sites": self.affected_sites,
            "defect_confidence": self.defect_confidence,
            "classification": self.classification,
            "selected_hypothesis_idx": self.selected_hypothesis_idx,
            "hypotheses": [h.to_dict() for h in self.hypotheses],
            "repair_sites": self.repair_sites,
        }


class CompetingDiagnosisEngine:
    """Constructs competing diagnosis prompt, parses hypotheses, and performs deterministic elimination."""

    @staticmethod
    def build_diagnostic_prompt(
        problem_statement: str,
        behavior_map: IssueBehaviorMap,
        candidate_profiles: List[CandidateProfile],
        failure_evidence: Optional[StructuredFailureEvidence] = None,
        state_flows: Optional[Dict[str, DeepStateFlowInfo]] = None,
        class_context: Optional[str] = None,
        execution_path_summary: str = "",
    ) -> str:
        """Constructs prompt for competing hypotheses with deterministic evidence substrate."""
        sections = []

        # 1. Behavioral Requirements
        sections.append(f"### ISSUE SPECIFICATION:\n{problem_statement}\n")
        sections.append(f"### STRUCTURED BEHAVIORAL REQUIREMENTS:\n{behavior_map.format_summary()}\n")

        # 2. Execution Path & Algorithmic Divergence
        if execution_path_summary:
            sections.append(f"{execution_path_summary}\n")

        # 3. Failure Evidence
        if failure_evidence and failure_evidence.failing_test:
            sections.append(f"### DETERMINISTIC EXECUTION FAILURE EVIDENCE:\n{failure_evidence.format_summary()}\n")

        # 3. Candidate Behavioral Profiles & Blast Radius
        cand_sec = ["### CANDIDATE REPAIR SITES & CAUSAL PATHS:"]
        for i, c in enumerate(candidate_profiles[:5], 1):
            cand_sec.append(f"\nCandidate {chr(64 + i)}: `{c.file_path}:{c.symbol}` ({c.ast_node_type}, L{c.start_line}-L{c.end_line})")
            cand_sec.append(f"  {c.blast_radius.format_summary()}")
            if c.causal_paths:
                cand_sec.append(f"  Causal Execution Path: {c.causal_paths[0].path_str}")
            if c.counterfactual:
                cand_sec.append(f"  Counterfactual Feasibility: {c.counterfactual.format_summary()}")
            if c.direct_tests:
                cand_sec.append(f"  Direct Tests: {', '.join(c.direct_tests[:4])}")
        sections.append("\n".join(cand_sec) + "\n")

        # 4. State Flow Summaries
        if state_flows:
            flow_sec = ["### DEEP STATE FLOW & MUTATIONS:"]
            for sym, s_flow in list(state_flows.items())[:3]:
                flow_sec.append(f"\n{s_flow.format_summary()}")
            sections.append("\n".join(flow_sec) + "\n")

        # 5. Bounded Class Context
        if class_context:
            sections.append(f"### BOUNDED REPOSITORY CONTEXT:\n```python\n{class_context[:10000]}\n```\n")

        # 6. Instructions
        instructions = """---
### COMPETING DIAGNOSIS INSTRUCTIONS:
Do NOT output a single hasty conclusion. You must formulate 2 to 3 COMPETING HYPOTHESES (Hypothesis A, Hypothesis B, etc.).
For each hypothesis, evaluate:
1. Exact Root Cause & Divergence Point
2. Invariant to preserve existing callers
3. Repair Strategy
4. Affected Sites (file_path:symbol)
5. Causal Prediction: Falsifiable predictions (expected branch, expected mutations, forbidden events, discriminating variable)
6. Supporting Evidence (from tests, causal paths, state flow)
7. Contradicting Evidence / Risks (e.g. high blast radius, unaffected tests)
8. Unexplained Symptoms (if any observed symptoms are not explained by this hypothesis)

Output your diagnosis strictly as JSON within a ```json ... ``` code fence:
```json
{
  "hypotheses": [
    {
      "id": "A",
      "cause": "...",
      "invariant": "...",
      "repair_strategy": "...",
      "affected_sites": ["<file_path>:<symbol>"],
      "causal_prediction": {
        "expected_branch": "...",
        "expected_mutations": ["..."],
        "forbidden_events": ["..."],
        "discriminating_variable": "..."
      },
      "supporting_evidence": ["..."],
      "contradicting_evidence": ["..."],
      "unexplained_symptoms": [],
      "confidence": 0.90
    },
    {
      "id": "B",
      "cause": "...",
      "invariant": "...",
      "repair_strategy": "...",
      "affected_sites": ["<file_path>:<symbol>"],
      "causal_prediction": {
        "expected_branch": "...",
        "expected_mutations": ["..."],
        "forbidden_events": ["..."],
        "discriminating_variable": "..."
      },
      "supporting_evidence": ["..."],
      "contradicting_evidence": ["..."],
      "unexplained_symptoms": [],
      "confidence": 0.70
    }
  ],
  "selected_hypothesis": 0,
  "repair_sites": ["<file_path>:<symbol>"]
}
```
"""
        sections.append(instructions)
        return "\n".join(sections)

    @classmethod
    def parse_competing_diagnosis(
        cls,
        response_text: str,
        candidate_profiles: List[CandidateProfile],
        failure_evidence: Optional[StructuredFailureEvidence] = None,
        behavior_map: Optional[IssueBehaviorMap] = None,
        causal_trace: Optional[Any] = None,
    ) -> CompetingDiagnosisResult:
        """Parses model response into hypotheses and runs deterministic elimination."""
        hypotheses: List[DiagnosisHypothesis] = []
        selected_idx = 0
        repair_sites: List[str] = []

        # 1. Strip reasoning think tags
        cleaned = response_text
        if "<think>" in cleaned:
            cleaned = cleaned.split("</think>")[-1].strip() if "</think>" in cleaned else cleaned.split("<think>")[-1].strip()

        # 2. Extract JSON
        json_cand = None
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.DOTALL)
        if m:
            try:
                json_cand = json.loads(m.group(1))
            except Exception:
                pass

        if not json_cand:
            start_b = cleaned.find("{")
            end_b = cleaned.rfind("}")
            if start_b != -1 and end_b > start_b:
                try:
                    json_cand = json.loads(cleaned[start_b : end_b + 1])
                except Exception:
                    pass

        # Parse structured JSON if present
        if isinstance(json_cand, dict) and "hypotheses" in json_cand:
            raw_hyps = json_cand.get("hypotheses", [])
            selected_idx = int(json_cand.get("selected_hypothesis", 0))
            repair_sites = [str(s) for s in json_cand.get("repair_sites", [])]

            for idx, rh in enumerate(raw_hyps):
                if isinstance(rh, dict):
                    pred_raw = rh.get("causal_prediction") or rh.get("prediction") or {}
                    prediction = None
                    if isinstance(pred_raw, dict) and pred_raw:
                        prediction = CausalPrediction(
                            expected_branch=str(pred_raw.get("expected_branch", "")),
                            expected_mutations=[str(m) for m in pred_raw.get("expected_mutations", [])],
                            expected_output_state=dict(pred_raw.get("expected_output_state", {})),
                            forbidden_events=[str(f) for f in pred_raw.get("forbidden_events", [])],
                            discriminating_variable=str(pred_raw.get("discriminating_variable", "")),
                        )

                    hypotheses.append(
                        DiagnosisHypothesis(
                            id=str(rh.get("id", chr(65 + idx))),
                            cause=str(rh.get("cause", "")),
                            divergent_predicate=str(rh.get("divergent_predicate", "")),
                            expected_transition=str(rh.get("expected_transition", "")),
                            prediction=prediction,
                            invariant=str(rh.get("invariant", "")),
                            repair_strategy=str(rh.get("repair_strategy", "")),
                            affected_sites=[str(s) for s in rh.get("affected_sites", [])],
                            supporting_evidence=[str(e) for e in rh.get("supporting_evidence", [])],
                            contradicting_evidence=[str(e) for e in rh.get("contradicting_evidence", [])],
                            unexplained_symptoms=[str(u) for u in rh.get("unexplained_symptoms", [])],
                            confidence=float(rh.get("confidence", 0.8)),
                        )
                    )

        # Fallback to parsing legacy text markers (=== CAUSE ===)
        if not hypotheses:
            hypotheses = cls._parse_legacy_text(cleaned, candidate_profiles)

        if not hypotheses:
            # Synthetic default hypothesis from top candidate
            top_cand = candidate_profiles[0] if candidate_profiles else None
            top_sym = f"{top_cand.file_path}:{top_cand.symbol}" if top_cand else "unknown"
            hypotheses.append(
                DiagnosisHypothesis(
                    id="A",
                    cause="Identified divergence in candidate behavioral path.",
                    invariant="Preserve callers and expected return types.",
                    repair_strategy="Apply targeted behavioral fix.",
                    affected_sites=[top_sym],
                    confidence=0.8,
                )
            )

        # 3. Deterministic Hypothesis Elimination & Ranking (Section 14)
        cls._eliminate_and_rank_hypotheses(
            hypotheses=hypotheses,
            candidate_profiles=candidate_profiles,
            failure_evidence=failure_evidence,
            behavior_map=behavior_map,
            causal_trace=causal_trace,
        )

        # Select top non-eliminated hypothesis
        active_hyps = [h for h in hypotheses if not h.eliminated]
        if active_hyps:
            active_hyps.sort(key=lambda h: h.counterfactual_score, reverse=True)
            winner = active_hyps[0]
            selected_idx = hypotheses.index(winner)
        else:
            hypotheses.sort(key=lambda h: h.counterfactual_score, reverse=True)
            winner = hypotheses[0]
            selected_idx = 0

        if not repair_sites and winner.affected_sites:
            repair_sites = winner.affected_sites

        return CompetingDiagnosisResult(
            hypotheses=hypotheses,
            selected_hypothesis_idx=selected_idx,
            selected_hypothesis=winner,
            repair_sites=repair_sites,
            raw_response=response_text,
            defect_confidence=winner.confidence,
        )

    @classmethod
    def _eliminate_and_rank_hypotheses(
        cls,
        hypotheses: List[DiagnosisHypothesis],
        candidate_profiles: List[CandidateProfile],
        failure_evidence: Optional[StructuredFailureEvidence],
        behavior_map: Optional[IssueBehaviorMap],
        causal_trace: Optional[Any] = None,
    ):
        """Applies deterministic repository facts to eliminate or boost hypotheses."""
        cand_by_sym = {c.symbol.split(".")[-1].lower(): c for c in candidate_profiles}

        tb_stack = [s.lower() for s in failure_evidence.call_stack] if failure_evidence else []

        for h in hypotheses:
            score = h.confidence * 100.0

            # Rule 1: Traceback reachability elimination (Section 14)
            # If hypothesis targets a site that is NOT in traceback when traceback is explicitly present
            if tb_stack and h.affected_sites:
                has_tb_match = False
                for site in h.affected_sites:
                    site_clean = site.split(":")[-1].split(".")[-1].lower()
                    if any(site_clean in frame for frame in tb_stack):
                        has_tb_match = True
                        break
                if not has_tb_match and len(tb_stack) >= 2:
                    score -= 40.0
                    h.contradicting_evidence.append(
                        f"Predicted target '{h.affected_sites}' does not appear in execution traceback stack."
                    )

            # Rule 2: Unexplained symptoms penalty (Section 9)
            if h.unexplained_symptoms:
                penalty = min(35.0, len(h.unexplained_symptoms) * 15.0)
                score -= penalty
                h.contradicting_evidence.append(
                    f"Fails to explain {len(h.unexplained_symptoms)} symptoms (-{penalty:.0f})"
                )

            # Rule 3: Shared helper vs call-site check (Section 6)
            for site in h.affected_sites:
                site_sym = site.split(":")[-1].split(".")[-1].lower()
                c_prof = cand_by_sym.get(site_sym)
                if c_prof:
                    if c_prof.blast_radius.classification == HelperClassification.GLOBAL_INFRASTRUCTURE:
                        score -= 25.0
                        h.contradicting_evidence.append(
                            f"Target '{site}' is GLOBAL_INFRASTRUCTURE with {c_prof.blast_radius.cross_module_callers_count} cross-module callers (-25)"
                        )
                    elif c_prof.blast_radius.classification == HelperClassification.FEATURE_LOCAL:
                        score += 20.0
                        h.supporting_evidence.append(
                            f"Target '{site}' is FEATURE_LOCAL with bounded blast radius (+20)"
                        )

            # Rule 5: Causal Prediction Verification against BehavioralTrace
            if h.prediction and causal_trace:
                pred = h.prediction
                # Check forbidden events against observed trace
                if pred.forbidden_events:
                    for fe in pred.forbidden_events:
                        fe_lower = fe.lower()
                        for event in getattr(causal_trace, "events", []):
                            if fe_lower in event.code_snippet.lower() or fe_lower in getattr(event, "predicate_evaluated", "").lower():
                                pred.contradiction_count += 1
                                h.contradiction_count += 1
                                score -= 30.0
                                h.contradicting_evidence.append(
                                    f"Causal Contradiction: Forbidden event '{fe}' was observed at L{event.line}"
                                )

                # Check expected branch against first divergence
                if pred.expected_branch and getattr(causal_trace, "divergence_transition", ""):
                    if any(tok in getattr(causal_trace, "divergence_transition", "").lower() for tok in pred.expected_branch.lower().split()):
                        pred.support_count += 1
                        h.support_count += 1
                        score += 15.0
                        h.supporting_evidence.append(
                            "Causal Support: Predicted branch matches observed divergence transition."
                        )

                # If severe contradictions dominate support
                if h.contradiction_count > h.support_count and h.contradiction_count >= 2:
                    h.eliminated = True
                    h.elimination_reason = f"Eliminated by causal contradiction: {h.contradiction_count} predictions contradicted observed trace."

            # Rule 4: Eliminate if score collapses
            h.counterfactual_score = round(score, 2)
            if score < 20.0:
                h.eliminated = True
                if not h.elimination_reason:
                    h.elimination_reason = "Eliminated by deterministic evidence (traceback divergence or severe blast radius)."

    @classmethod
    def _parse_legacy_text(
        cls, text: str, candidate_profiles: List[CandidateProfile]
    ) -> List[DiagnosisHypothesis]:
        """Parses legacy === CAUSE === format into a single hypothesis."""
        cause = ""
        expected = ""
        invariant = ""
        strategy = ""
        affected = []

        c_match = re.search(r"===\s*CAUSE\s*===(.*?)(?====|\Z)", text, re.DOTALL)
        if c_match:
            cause = c_match.group(1).strip()
        e_match = re.search(r"===\s*EXPECTED BEHAVIOR\s*===(.*?)(?====|\Z)", text, re.DOTALL)
        if e_match:
            expected = e_match.group(1).strip()
        i_match = re.search(r"===\s*INVARIANT\s*===(.*?)(?====|\Z)", text, re.DOTALL)
        if i_match:
            invariant = i_match.group(1).strip()
        r_match = re.search(r"===\s*REPAIR STRATEGY\s*===(.*?)(?====|\Z)", text, re.DOTALL)
        if r_match:
            strategy = r_match.group(1).strip()
        a_match = re.search(r"===\s*AFFECTED SITES\s*===(.*?)(?====|\Z)", text, re.DOTALL)
        if a_match:
            for l in a_match.group(1).strip().splitlines():
                cl = l.strip("-*` \t")
                if cl and not cl.startswith("#"):
                    affected.append(cl)

        if not cause and candidate_profiles:
            cause = "Behavioral divergence identified in target candidate."

        return [
            DiagnosisHypothesis(
                id="A",
                cause=cause,
                invariant=invariant or "Preserve existing callers.",
                repair_strategy=strategy or "Apply targeted repair.",
                affected_sites=affected,
                confidence=0.85,
            )
        ]
