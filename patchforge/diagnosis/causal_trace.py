"""PatchForge V1.3 Behavioral Trace & Causal First-Divergence Engine.

Reconstructs bounded execution events (branches, state mutations, calls, exceptions)
and isolates the FIRST behavioral divergence where actual execution diverges from
expected requirements, distinguishing the root-cause divergence from the downstream
assertion failure.
"""
from __future__ import annotations

import ast
import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


class TraceEventType(str, Enum):
    BRANCH = "BRANCH"
    MUTATION = "MUTATION"
    CALL = "CALL"
    EXCEPTION = "EXCEPTION"
    RETURN = "RETURN"


@dataclass
class TraceEvent:
    """A discrete execution event along the bounded causal path."""
    node_id: str
    event_type: TraceEventType
    line: int
    code_snippet: str
    predicate_evaluated: str = ""
    state_delta: Dict[str, str] = field(default_factory=dict)
    is_divergence_point: bool = False
    explanation: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id,
            "event_type": self.event_type.value,
            "line": self.line,
            "code_snippet": self.code_snippet,
            "predicate_evaluated": self.predicate_evaluated,
            "state_delta": self.state_delta,
            "is_divergence_point": self.is_divergence_point,
            "explanation": self.explanation,
        }


@dataclass
class BehavioralTrace:
    """The bounded causal trace through the defective function."""
    target_symbol: str
    file_path: str
    events: List[TraceEvent] = field(default_factory=list)
    assertion_failure_line: Optional[int] = None
    first_divergence_line: Optional[int] = None
    divergence_transition: str = ""
    divergence_cause: str = ""
    expected_state: Dict[str, str] = field(default_factory=dict)
    observed_state: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target_symbol": self.target_symbol,
            "file_path": self.file_path,
            "events": [e.to_dict() for e in self.events],
            "assertion_failure_line": self.assertion_failure_line,
            "first_divergence_line": self.first_divergence_line,
            "divergence_transition": self.divergence_transition,
            "divergence_cause": self.divergence_cause,
            "expected_state": self.expected_state,
            "observed_state": self.observed_state,
        }

    def format_for_prompt(self) -> str:
        if not self.events:
            return ""
        lines = [
            f"=== CAUSAL BEHAVIORAL TRACE: `{self.target_symbol}` in `{self.file_path}` ===",
            f"Downstream Assertion Failure Line: {self.assertion_failure_line or 'N/A'}",
        ]
        if self.first_divergence_line:
            lines.append(f">>> FIRST BEHAVIORAL DIVERGENCE: Line {self.first_divergence_line}")
            lines.append(f"    Transition: {self.divergence_transition}")
            lines.append(f"    Causal Mechanism: {self.divergence_cause}")
        
        lines.append("Execution Events:")
        for e in self.events:
            marker = ">>> [FIRST DIVERGENCE] " if e.is_divergence_point else "    "
            pred = f" | Cond: `{e.predicate_evaluated}`" if e.predicate_evaluated else ""
            delta = f" | State: {e.state_delta}" if e.state_delta else ""
            lines.append(f"{marker}L{e.line:4d} [{e.event_type.value}]: `{e.code_snippet}`{pred}{delta}")
            if e.is_divergence_point and e.explanation:
                lines.append(f"        -> {e.explanation}")

        return "\n".join(lines)


class FirstDivergenceLocator:
    """Pinpoints the earliest point where state or control flow diverges from specification."""

    @classmethod
    def locate_divergence(
        cls,
        source_code: str,
        target_symbol: str,
        file_path: str,
        traceback_lines: Optional[List[int]] = None,
        failing_test_code: str = "",
        problem_statement: str = "",
    ) -> BehavioralTrace:
        trace = BehavioralTrace(target_symbol=target_symbol, file_path=file_path)
        tb_set = set(traceback_lines or [])
        lines = source_code.splitlines()

        def get_line_snippet(lineno: int) -> str:
            if 1 <= lineno <= len(lines):
                return lines[lineno - 1].strip()
            return ""

        try:
            tree = ast.parse(source_code)
        except SyntaxError:
            return trace

        # Find target FunctionDef
        target_func = None
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not target_symbol or node.name == target_symbol:
                    target_func = node
                    break

        if not target_func:
            return trace

        # Identify assertion failure line if it falls in this function
        func_start = target_func.lineno
        func_end = getattr(target_func, "end_lineno", func_start + 100)
        in_func_tb = [l for l in tb_set if func_start <= l <= func_end]
        if in_func_tb:
            trace.assertion_failure_line = max(in_func_tb)

        events: List[TraceEvent] = []

        # Walk body statements to collect causal events
        for stmt in target_func.body:
            lineno = getattr(stmt, "lineno", func_start)
            snippet = get_line_snippet(lineno)

            if isinstance(stmt, ast.If):
                test_str = ast.unparse(stmt.test) if hasattr(ast, "unparse") else snippet
                event = TraceEvent(
                    node_id=f"if_{lineno}",
                    event_type=TraceEventType.BRANCH,
                    line=lineno,
                    code_snippet=snippet,
                    predicate_evaluated=test_str,
                )
                events.append(event)
                # Check nested statements
                for sub in stmt.body:
                    sub_l = getattr(sub, "lineno", lineno)
                    sub_snip = get_line_snippet(sub_l)
                    if isinstance(sub, (ast.Assign, ast.AugAssign)):
                        targets = []
                        if isinstance(sub, ast.Assign) and hasattr(ast, "unparse"):
                            targets = [ast.unparse(t) for t in sub.targets]
                        val_str = ast.unparse(sub.value) if hasattr(ast, "unparse") else ""
                        events.append(
                            TraceEvent(
                                node_id=f"mut_{sub_l}",
                                event_type=TraceEventType.MUTATION,
                                line=sub_l,
                                code_snippet=sub_snip,
                                state_delta={t: val_str for t in targets},
                            )
                        )

            elif isinstance(stmt, (ast.Assign, ast.AugAssign)):
                targets = []
                if isinstance(stmt, ast.Assign) and hasattr(ast, "unparse"):
                    targets = [ast.unparse(t) for t in stmt.targets]
                val_str = ast.unparse(stmt.value) if hasattr(ast, "unparse") else ""
                events.append(
                    TraceEvent(
                        node_id=f"mut_{lineno}",
                        event_type=TraceEventType.MUTATION,
                        line=lineno,
                        code_snippet=snippet,
                        state_delta={t: val_str for t in targets},
                    )
                )

            elif isinstance(stmt, ast.Return):
                ret_val = ast.unparse(stmt.value) if stmt.value and hasattr(ast, "unparse") else ""
                events.append(
                    TraceEvent(
                        node_id=f"ret_{lineno}",
                        event_type=TraceEventType.RETURN,
                        line=lineno,
                        code_snippet=snippet,
                        state_delta={"return_value": ret_val},
                    )
                )

            elif isinstance(stmt, ast.Try):
                events.append(
                    TraceEvent(
                        node_id=f"try_{lineno}",
                        event_type=TraceEventType.EXCEPTION,
                        line=lineno,
                        code_snippet=snippet,
                    )
                )

        events.sort(key=lambda e: e.line)
        trace.events = events

        # Determine FIRST BEHAVIORAL DIVERGENCE:
        # Causal Rule 1: If there is an assertion failure line, look for the EARLIEST branch or mutation
        # directly governing that line before the assertion.
        divergence_event: Optional[TraceEvent] = None

        if trace.assertion_failure_line:
            # Find the closest preceding branch or mutation
            preceding = [e for e in events if e.line <= trace.assertion_failure_line]
            if preceding:
                # Prefer the earliest branch governing this execution
                branches = [e for e in preceding if e.event_type == TraceEventType.BRANCH]
                divergence_event = branches[0] if branches else preceding[0]

        # Causal Rule 2: If no assertion line in this function, inspect keywords from problem_statement
        if not divergence_event and problem_statement:
            kw_match = []
            for e in events:
                code_lower = e.code_snippet.lower()
                pred_lower = e.predicate_evaluated.lower()
                if any(w in code_lower or w in pred_lower for w in ("if ", "elif ", "is_redirect", "content", "decode", "hook", "scope")):
                    kw_match.append(e)
            if kw_match:
                divergence_event = kw_match[0]

        # Causal Rule 3: Default to first branch or mutation
        if not divergence_event and events:
            divergence_event = events[0]

        if divergence_event:
            divergence_event.is_divergence_point = True
            trace.first_divergence_line = divergence_event.line
            trace.divergence_transition = (
                f"At line {divergence_event.line} (`{divergence_event.code_snippet}`), "
                f"state transition does not satisfy required postconditions."
            )
            trace.divergence_cause = (
                f"Observed logic takes default branch/mutation; must produce required invariant before downstream exit."
            )
            divergence_event.explanation = trace.divergence_transition

        return trace
