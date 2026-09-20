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
Before writing any code or patches, you must diagnose the root cause of the defect and define the regression invariant.

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

=== INVARIANT ===
State the strict behavioral invariant that must be maintained so existing callers and regression tests do not break.

=== REPAIR STRATEGY ===
Outline the step-by-step repair strategy to fix the defect cleanly without side effects.

=== AFFECTED SITES ===
List the file paths and function/method names that must be modified (one per line).
"""
        return prompt

    @staticmethod
    def parse_diagnosis(response_text: str) -> DiagnosisResult:
        """Parses model diagnostic output into structured fields."""
        cause = ""
        invariant = ""
        strategy = ""
        affected_sites = []

        cause_match = re.search(r"=== CAUSE ===\s*(.*?)(?==== INVARIANT ===|\Z)", response_text, re.DOTALL)
        if cause_match:
            cause = cause_match.group(1).strip()

        inv_match = re.search(r"=== INVARIANT ===\s*(.*?)(?==== REPAIR STRATEGY ===|\Z)", response_text, re.DOTALL)
        if inv_match:
            invariant = inv_match.group(1).strip()

        strat_match = re.search(r"=== REPAIR STRATEGY ===\s*(.*?)(?==== AFFECTED SITES ===|\Z)", response_text, re.DOTALL)
        if strat_match:
            strategy = strat_match.group(1).strip()

        sites_match = re.search(r"=== AFFECTED SITES ===\s*(.*)", response_text, re.DOTALL)
        if sites_match:
            raw_sites = sites_match.group(1).strip()
            for line in raw_sites.splitlines():
                clean_line = line.strip().lstrip("*- ")
                if clean_line and not clean_line.startswith("="):
                    affected_sites.append(clean_line)

        return DiagnosisResult(
            cause=cause or "Identified behavior mismatch in primary target.",
            invariant=invariant or "Preserve existing return signatures and error specifications.",
            repair_strategy=strategy or "Update conditional logic and exception handling.",
            affected_sites=affected_sites,
            raw_response=response_text,
        )
