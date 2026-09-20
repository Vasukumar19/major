"""Behavioral Diagnosis Workspace: structured causal reasoning prior to patch synthesis."""
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from patchforge.repository_intelligence.retriever import RepairContext

logger = logging.getLogger(__name__)


@dataclass
class DiagnosisResult:
    cause: str
    invariant: str
    repair_strategy: str
    affected_sites: List[str]
    raw_response: str
    defect_confidence: float = 0.95
    expected_behavior: str = ""
    evidence: List[str] = field(default_factory=list)
    repair_justification: str = ""
    classification: str = "confirmed_code_defect"  # confirmed_code_defect, behavioral_mismatch, ambiguous, documentation_issue, test_only_issue

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cause": self.cause,
            "invariant": self.invariant,
            "repair_strategy": self.repair_strategy,
            "affected_sites": self.affected_sites,
            "defect_confidence": self.defect_confidence,
            "expected_behavior": self.expected_behavior,
            "evidence": self.evidence,
            "repair_justification": self.repair_justification,
            "classification": self.classification,
        }


class BehavioralDiagnosisEngine:
    """Constructs the structured diagnostic workspace prompt and parses the causal diagnosis."""

    @staticmethod
    def build_diagnostic_prompt(
        problem_statement: str,
        repair_context: RepairContext,
        failure_traceback: Optional[str] = None,
    ) -> str:
        """Constructs a structured diagnostic workspace preventing direct leap-to-patch errors."""
        context_str = repair_context.to_diagnostic_prompt()

        traceback_sec = ""
        if failure_traceback:
            traceback_sec = f"\n### EXECUTION FAILURE TRACEBACK:\n```text\n{failure_traceback[:2000]}\n```\n"

        prompt = f"""You are the Principal Software Diagnosis Engine for PatchForge.
Before writing any code or patches, you must diagnose the root cause of the defect, gather concrete evidence, and define the regression invariant.

### ISSUE SPECIFICATION:
{problem_statement}
{traceback_sec}
{context_str}

---
### DIAGNOSTIC INSTRUCTIONS:
Analyze the structural graph, control flow, state flow, and issue specification above.
You must provide your diagnosis in the following exact format:

=== CAUSE ===
Explain the exact root cause: where does the code diverge from expected behavior, what variable or branch causes it, and why?

=== EXPECTED BEHAVIOR ===
Describe what the code should do instead according to specifications and invariants.

=== INVARIANT ===
State the strict behavioral invariant that must be maintained so existing callers and regression tests do not break.

=== EVIDENCE ===
List concrete repository evidence proving the defect (failing test assertions, violated protocol, caller expectations, state transitions).

=== DEFECT CONFIDENCE ===
State your confidence level (e.g. 0.95) and classification (confirmed_code_defect, behavioral_mismatch, ambiguous).
NOTE: For confirmed repository defect reports, you MUST identify the code repair required. Do not propose documentation-only or testing-only resolutions without explicit proof that the existing code is already completely correct.

=== REPAIR STRATEGY ===
Outline the step-by-step repair strategy to fix the defect cleanly without side effects.

=== REPAIR JUSTIFICATION ===
Explain why this repair strategy satisfies the invariant and avoids breaking existing callers.

=== AFFECTED SITES ===
List the file paths and function/method names that must be modified (one per line).
"""
        return prompt

    @staticmethod
    def parse_diagnosis(response_text: str) -> DiagnosisResult:
        """Parses model diagnostic output into structured fields."""
        cause = ""
        expected = ""
        invariant = ""
        evidence = []
        confidence = 0.95
        classification = "confirmed_code_defect"
        strategy = ""
        justification = ""
        affected_sites = []

        def get_section(name: str, next_names: List[str]) -> str:
            if next_names:
                nxt_pattern = "|".join([rf"=== {nxt} ===" for nxt in next_names])
                pattern = rf"=== {name} ===\s*(.*?)(?={nxt_pattern}|\Z)"
            else:
                pattern = rf"=== {name} ===\s*(.*)"
            match = re.search(pattern, response_text, re.DOTALL | re.IGNORECASE)
            return match.group(1).strip() if match else ""

        cause = get_section("CAUSE", ["EXPECTED BEHAVIOR", "INVARIANT", "EVIDENCE"])
        expected = get_section("EXPECTED BEHAVIOR", ["INVARIANT", "EVIDENCE", "DEFECT CONFIDENCE"])
        invariant = get_section("INVARIANT", ["EVIDENCE", "DEFECT CONFIDENCE", "REPAIR STRATEGY"])
        raw_evidence = get_section("EVIDENCE", ["DEFECT CONFIDENCE", "REPAIR STRATEGY", "REPAIR JUSTIFICATION"])
        raw_confidence = get_section("DEFECT CONFIDENCE", ["REPAIR STRATEGY", "REPAIR JUSTIFICATION", "AFFECTED SITES"])
        strategy = get_section("REPAIR STRATEGY", ["REPAIR JUSTIFICATION", "AFFECTED SITES"])
        justification = get_section("REPAIR JUSTIFICATION", ["AFFECTED SITES"])
        raw_sites = get_section("AFFECTED SITES", [])

        # Parse evidence list
        if raw_evidence:
            for line in raw_evidence.splitlines():
                cl = line.strip().lstrip("*- 123456789.)")
                if cl:
                    evidence.append(cl)

        # Parse confidence float & classification
        if raw_confidence:
            conf_match = re.search(r"(\d+(?:\.\d+)?)", raw_confidence)
            if conf_match:
                try:
                    val = float(conf_match.group(1))
                    if 0.0 <= val <= 1.0:
                        confidence = val
                    elif 1.0 < val <= 100.0:
                        confidence = val / 100.0
                except ValueError:
                    pass
            for cls_cand in ["confirmed_code_defect", "behavioral_mismatch", "ambiguous", "documentation_issue", "test_only_issue"]:
                if cls_cand in raw_confidence.lower():
                    classification = cls_cand
                    break

        # Parse affected sites
        if raw_sites:
            for line in raw_sites.splitlines():
                clean_line = line.strip().lstrip("*- ")
                if clean_line and not clean_line.startswith("="):
                    affected_sites.append(clean_line)

        # Fallback if strategy was empty but cause exists
        if not strategy and cause:
            strategy = "Update defective conditional branch or exception handling to satisfy invariant."

        return DiagnosisResult(
            cause=cause or "Identified behavior mismatch in primary target.",
            invariant=invariant or "Preserve existing return signatures and error specifications.",
            repair_strategy=strategy or "Update conditional logic and exception handling.",
            affected_sites=affected_sites,
            raw_response=response_text,
            defect_confidence=confidence,
            expected_behavior=expected,
            evidence=evidence,
            repair_justification=justification,
            classification=classification,
        )
