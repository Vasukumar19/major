"""Structured Program Understanding model for PatchForge v0.3."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from patchforge.issue.problem import Problem
from patchforge.models.provider import ModelProvider


@dataclass
class ProgramUnderstanding:
    """Explicit semantic model of expected vs. observed program behavior."""
    summary: str = ""
    expected_behavior: str = ""
    observed_behavior: str = ""
    behavioral_delta: str = ""
    violated_invariants: list[str] = field(default_factory=list)
    key_symbols: list[str] = field(default_factory=list)
    key_files: list[str] = field(default_factory=list)
    confidence: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "expected_behavior": self.expected_behavior,
            "observed_behavior": self.observed_behavior,
            "behavioral_delta": self.behavioral_delta,
            "violated_invariants": self.violated_invariants,
            "key_symbols": self.key_symbols,
            "key_files": self.key_files,
            "confidence": self.confidence,
        }

    def format_summary(self) -> str:
        invariants = "\n".join(f"  - {inv}" for inv in self.violated_invariants) if self.violated_invariants else "  - None identified"
        return (
            f"SUMMARY: {self.summary}\n"
            f"EXPECTED BEHAVIOR: {self.expected_behavior}\n"
            f"OBSERVED BEHAVIOR: {self.observed_behavior}\n"
            f"BEHAVIORAL DELTA: {self.behavioral_delta}\n"
            f"VIOLATED INVARIANTS:\n{invariants}\n"
            f"KEY FILES: {', '.join(self.key_files) or 'None'}\n"
            f"KEY SYMBOLS: {', '.join(self.key_symbols) or 'None'}"
        )


UNDERSTANDING_SYSTEM_PROMPT = (
    "You are an expert software engineer analyzing a bug report. "
    "Synthesize the issue into a structured understanding of expected vs observed behavior. "
    "Output valid JSON matching this schema:\n"
    "{\n"
    '  "summary": "one-line summary of bug",\n'
    '  "expected_behavior": "what should happen",\n'
    '  "observed_behavior": "what actually happens / error message",\n'
    '  "behavioral_delta": "exact divergence between expected and observed",\n'
    '  "violated_invariants": ["list of broken invariants/assumptions"],\n'
    '  "key_symbols": ["functions or classes involved"],\n'
    '  "key_files": ["likely files involved"],\n'
    '  "confidence": 0.85\n'
    "}"
)


def derive_understanding(problem: Problem, provider: ModelProvider, model: str = "") -> ProgramUnderstanding:
    """Analyze the problem statement using LLM to derive structured ProgramUnderstanding."""
    prompt = (
        f"ISSUE DESCRIPTION:\n{problem.problem_statement.strip()[:3500]}\n\n"
        f"FAILING TESTS: {', '.join(problem.fail_to_pass[:5]) if problem.fail_to_pass else 'N/A'}\n"
    )
    resp = provider.generate_one(prompt, system=UNDERSTANDING_SYSTEM_PROMPT)
    text = resp.text.strip()
    
    # Clean possible markdown block
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])

    try:
        data = json.loads(text)
        return ProgramUnderstanding(
            summary=data.get("summary", ""),
            expected_behavior=data.get("expected_behavior", ""),
            observed_behavior=data.get("observed_behavior", ""),
            behavioral_delta=data.get("behavioral_delta", ""),
            violated_invariants=data.get("violated_invariants", []),
            key_symbols=data.get("key_symbols", []),
            key_files=data.get("key_files", []),
            confidence=float(data.get("confidence", 0.8)),
        )
    except Exception:
        # Fallback extraction from raw problem statement
        return ProgramUnderstanding(
            summary=problem.problem_statement.strip().split("\n")[0][:120],
            expected_behavior="Code should execute without raising unexpected exceptions or failing assertions.",
            observed_behavior=problem.problem_statement.strip()[:300],
            behavioral_delta="Unexpected failure during operation.",
            violated_invariants=["Execution invariant violated."],
            key_files=problem.hints_text.split()[:2] if hasattr(problem, "hints_text") else [],
            confidence=0.5,
        )
