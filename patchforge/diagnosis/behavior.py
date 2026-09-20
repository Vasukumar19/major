"""Issue-to-Behavior Mapping: converts unstructured issue descriptions into structured behavioral requirements."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


@dataclass
class IssueBehaviorMap:
    """Structured behavioral requirements extracted from an issue description."""
    expected_behavior: str = ""
    current_behavior: str = ""
    trigger: str = ""
    input_conditions: List[str] = field(default_factory=list)
    output_conditions: List[str] = field(default_factory=list)
    exceptions: List[str] = field(default_factory=list)
    state_changes: List[str] = field(default_factory=list)
    affected_api: List[str] = field(default_factory=list)
    mentioned_symbols: List[str] = field(default_factory=list)
    mentioned_files: List[str] = field(default_factory=list)
    mentioned_tests: List[str] = field(default_factory=list)
    symptoms: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "expected_behavior": self.expected_behavior,
            "current_behavior": self.current_behavior,
            "trigger": self.trigger,
            "input_conditions": self.input_conditions,
            "output_conditions": self.output_conditions,
            "exceptions": self.exceptions,
            "state_changes": self.state_changes,
            "affected_api": self.affected_api,
            "mentioned_symbols": self.mentioned_symbols,
            "mentioned_files": self.mentioned_files,
            "mentioned_tests": self.mentioned_tests,
            "symptoms": self.symptoms,
        }

    def format_summary(self) -> str:
        lines = ["Structured Behavioral Requirements:"]
        if self.expected_behavior:
            lines.append(f"  - Expected Behavior: {self.expected_behavior}")
        if self.current_behavior:
            lines.append(f"  - Current / Observed Behavior: {self.current_behavior}")
        if self.trigger:
            lines.append(f"  - Trigger Condition: {self.trigger}")
        if self.symptoms:
            lines.append(f"  - Observed Symptoms ({len(self.symptoms)}):")
            for s in self.symptoms[:5]:
                lines.append(f"      * {s}")
        if self.exceptions:
            lines.append(f"  - Implicated Exceptions: {', '.join(self.exceptions)}")
        if self.affected_api:
            lines.append(f"  - Implicated APIs / Symbols: {', '.join(self.affected_api[:6])}")
        if self.mentioned_files:
            lines.append(f"  - Implicated Files: {', '.join(self.mentioned_files[:4])}")
        if self.mentioned_tests:
            lines.append(f"  - Implicated Tests: {', '.join(self.mentioned_tests[:4])}")
        return "\n".join(lines)


class IssueBehaviorExtractor:
    """Extracts structured behavioral requirements from issue statements and hints."""

    # Python exceptions pattern
    EXCEPTION_PATTERN = re.compile(r"\b([A-Z][a-zA-Z0-9]*(?:Error|Exception|Warning|Interrupt|Exit))\b")
    # File path pattern
    FILE_PATTERN = re.compile(r"([a-zA-Z0-9_\-\./\\]+\.py)\b")
    # Test function pattern
    TEST_PATTERN = re.compile(r"\b(test_[a-zA-Z0-9_]+|[a-zA-Z0-9_]+_test)\b")
    # Method/function pattern: `symbol(...)` or `Class.symbol` or `symbol()`
    CALL_PATTERN = re.compile(r"`?([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+)`?|`([A-Za-z_][A-Za-z0-9_]*)\(\)`")
    # Python code backticks
    BACKTICK_PATTERN = re.compile(r"`([A-Za-z0-9_\.\(\)]+)`")

    @classmethod
    def extract(cls, problem_statement: str, hints_text: str = "") -> IssueBehaviorMap:
        text = f"{problem_statement}\n{hints_text}"
        lines = [line.strip() for line in text.splitlines() if line.strip()]

        # 1. Exceptions
        exceptions = sorted(set(cls.EXCEPTION_PATTERN.findall(text)))

        # 2. Mentioned files
        files = set()
        for m in cls.FILE_PATTERN.findall(text):
            cleaned = m.replace("\\", "/").strip("./")
            if not cleaned.startswith("test") and "/" in cleaned:
                files.add(cleaned)
            elif cleaned.endswith(".py"):
                files.add(cleaned)
        mentioned_files = sorted(files)

        # 3. Mentioned tests
        mentioned_tests = sorted(set(cls.TEST_PATTERN.findall(text)))

        # 4. Mentioned symbols and affected API
        symbols = set()
        for call_m, func_m in cls.CALL_PATTERN.findall(text):
            sym = call_m or func_m
            if sym and not sym.endswith(".py") and len(sym) > 2:
                symbols.add(sym)

        for bt in cls.BACKTICK_PATTERN.findall(text):
            bt_clean = bt.strip("()")
            if re.match(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$", bt_clean):
                if bt_clean not in exceptions and not bt_clean.endswith(".py") and len(bt_clean) > 2:
                    symbols.add(bt_clean)

        mentioned_symbols = sorted(symbols)
        affected_api = [s for s in mentioned_symbols if "." in s or any(kw in s.lower() for kw in ("get", "post", "set", "send", "load", "parse", "init", "format", "validate", "prepare"))]
        if not affected_api and mentioned_symbols:
            affected_api = mentioned_symbols[:5]

        # 5. Extract expected vs current behavior & trigger
        expected_candidates: List[str] = []
        current_candidates: List[str] = []
        trigger_candidates: List[str] = []
        symptoms: List[str] = []

        expected_patterns = [
            r"(?:expected|should|ought to|supposed to|needs to|must)\s+([^.\n]+)",
            r"(?:expected behavior:?\s*)([^\n]+)",
        ]
        current_patterns = [
            r"(?:instead|currently|actual|fails with|raises|returns|got)\s+([^.\n]+)",
            r"(?:actual behavior:?\s*)([^\n]+)",
        ]
        trigger_patterns = [
            r"(?:when|if|upon|calling|invoking)\s+([^.\n]+)",
        ]

        for line in lines:
            # Check for symptom list items (bullets, numbered, or traceback lines)
            if line.startswith(("-", "*", "1.", "2.", "3.", "4.", "5.")) or "assert " in line or "Error:" in line or "Traceback" in line:
                symptom_text = line.lstrip("-*0123456789. ").strip()
                if len(symptom_text) > 5 and symptom_text not in symptoms:
                    symptoms.append(symptom_text)

            for p in expected_patterns:
                m = re.search(p, line, re.IGNORECASE)
                if m:
                    cand = m.group(1).strip()
                    if len(cand) > 8 and cand not in expected_candidates:
                        expected_candidates.append(cand)

            for p in current_patterns:
                m = re.search(p, line, re.IGNORECASE)
                if m:
                    cand = m.group(1).strip()
                    if len(cand) > 8 and cand not in current_candidates:
                        current_candidates.append(cand)

            for p in trigger_patterns:
                m = re.search(p, line, re.IGNORECASE)
                if m:
                    cand = m.group(1).strip()
                    if len(cand) > 8 and cand not in trigger_candidates:
                        trigger_candidates.append(cand)

        expected_behavior = "; ".join(expected_candidates[:3]) if expected_candidates else (lines[0] if lines else "")
        current_behavior = "; ".join(current_candidates[:3]) if current_candidates else ""
        trigger = "; ".join(trigger_candidates[:2]) if trigger_candidates else ""

        # 6. State changes keywords
        state_keywords = ["redirect", "cookie", "session", "header", "state", "cache", "mutate", "overwrite", "reset", "clear", "persist", "copy", "environ", "stream", "chunk"]
        state_changes = [kw for kw in state_keywords if kw in text.lower()]

        # 7. Input/Output conditions
        input_conditions = []
        output_conditions = []
        if "none" in text.lower():
            input_conditions.append("None / empty input")
        if "empty" in text.lower():
            input_conditions.append("Empty parameter or collection")
        if "url" in text.lower() or "scheme" in text.lower():
            input_conditions.append("URL / scheme parameter")
        if "dict" in text.lower() or "mapping" in text.lower():
            input_conditions.append("Dictionary / mapping input")
        if "200" in text or "status" in text.lower():
            output_conditions.append("HTTP status code")
        if exceptions:
            output_conditions.append(f"Exception handling ({', '.join(exceptions[:3])})")

        return IssueBehaviorMap(
            expected_behavior=expected_behavior,
            current_behavior=current_behavior,
            trigger=trigger,
            input_conditions=input_conditions,
            output_conditions=output_conditions,
            exceptions=exceptions,
            state_changes=state_changes,
            affected_api=affected_api,
            mentioned_symbols=mentioned_symbols,
            mentioned_files=mentioned_files,
            mentioned_tests=mentioned_tests,
            symptoms=symptoms[:10],
        )
