"""Behavioral Evidence Engine: deterministic analysis of failure tracebacks, state flows, test graphs, and bounded large-class context."""
from __future__ import annotations

import ast
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from patchforge.diagnosis.behavior import IssueBehaviorMap
from patchforge.diagnosis.candidate_analysis import CandidateBehaviorAnalyzer, CandidateProfile
from patchforge.repository_intelligence.flow import StateFlowAnalyzer, StateFlowSummary
from patchforge.repository_intelligence.graph import RepositoryGraph
from patchforge.repository_intelligence.test_graph import TestCaseEntity, TestGraphIndex

logger = logging.getLogger(__name__)


@dataclass
class StructuredFailureEvidence:
    """Deterministic extraction from execution failure or test traceback."""
    failing_test: str = ""
    failing_file: str = ""
    failing_line: int = 0
    error_type: str = ""
    error_message: str = ""
    assertion_statement: str = ""
    expected_value: str = ""
    actual_value: str = ""
    call_stack: List[str] = field(default_factory=list)
    state_difference: str = ""

    def format_summary(self) -> str:
        lines = ["Execution Failure Evidence:"]
        if self.failing_test:
            lines.append(f"  - Failing Test: `{self.failing_test}`")
        if self.error_type or self.error_message:
            lines.append(f"  - Error: {self.error_type}: {self.error_message}")
        if self.assertion_statement:
            lines.append(f"  - Violated Assertion: `{self.assertion_statement}`")
        if self.expected_value or self.actual_value:
            lines.append(f"  - State Divergence: Expected `{self.expected_value}` vs Actual `{self.actual_value}`")
        if self.call_stack:
            lines.append(f"  - Call Stack Trace: {' -> '.join(self.call_stack[:5])}")
        return "\n".join(lines)


@dataclass
class DeepStateFlowInfo:
    """Rich state mutation and propagation analysis within a candidate method."""
    function_name: str
    parameters: List[str]
    overwritten_vars: List[str] = field(default_factory=list)
    mutated_attributes: List[str] = field(default_factory=list)
    conditionally_modified: List[str] = field(default_factory=list)
    returned_vars: List[str] = field(default_factory=list)
    loop_carried: List[str] = field(default_factory=list)
    key_transitions: List[str] = field(default_factory=list)

    def format_summary(self) -> str:
        lines = [f"Deep State Flow for `{self.function_name}`:"]
        lines.append(f"  - Parameters: {', '.join(self.parameters) if self.parameters else 'None'}")
        if self.overwritten_vars:
            lines.append(f"  - Overwritten / Reset State: {', '.join(self.overwritten_vars)}")
        if self.mutated_attributes:
            lines.append(f"  - Mutated Attributes/Containers: {', '.join(self.mutated_attributes)}")
        if self.conditionally_modified:
            lines.append(f"  - Conditionally Modified Variables: {', '.join(self.conditionally_modified)}")
        if self.returned_vars:
            lines.append(f"  - Returned State: {', '.join(self.returned_vars)}")
        if self.key_transitions:
            lines.append("  - Key State Transitions:")
            for t in self.key_transitions[:8]:
                lines.append(f"      * {t}")
        return "\n".join(lines)


@dataclass
class BoundedClassContext:
    """Surgical context representation for large multi-method classes."""
    class_name: str
    file_path: str
    start_line: int
    end_line: int
    class_header: str
    relevant_methods_source: Dict[str, str] = field(default_factory=dict)
    sibling_stubs: List[str] = field(default_factory=list)

    def format_for_prompt(self) -> str:
        lines = [f"Class {self.class_name} ({self.file_path}:L{self.start_line}-L{self.end_line}):"]
        lines.append(self.class_header)
        lines.append("\n  # --- Active Relevant Methods Participating in Causal Paths ---")
        for sym, src in self.relevant_methods_source.items():
            lines.append(f"\n  # Method: {sym}\n{src}")
        if self.sibling_stubs:
            lines.append("\n  # --- Sibling Methods (Context Only) ---")
            for stub in self.sibling_stubs[:12]:
                lines.append(f"  {stub}")
        return "\n".join(lines)


class BehavioralEvidenceEngine:
    """Coordinates deterministic evidence generation across graph, tracebacks, state flows, and tests."""

    def __init__(
        self,
        repo_dir: str,
        graph: Optional[RepositoryGraph] = None,
        test_index: Optional[TestGraphIndex] = None,
    ):
        self.repo_dir = Path(repo_dir)
        self.graph = graph
        self.test_index = test_index
        self.candidate_analyzer = CandidateBehaviorAnalyzer(graph, test_index)

    def parse_failure_traceback(self, traceback_text: str) -> StructuredFailureEvidence:
        """Parses execution failure or pytest traceback into structured evidence."""
        if not traceback_text:
            return StructuredFailureEvidence()

        failing_test = ""
        failing_file = ""
        failing_line = 0
        error_type = ""
        error_msg = ""
        assertion_stmt = ""
        expected = ""
        actual = ""
        call_stack = []

        # 1. Failing test name
        test_m = re.search(r"_{4,}\s+([a-zA-Z0-9_]+(?:\.[a-zA-Z0-9_]+)?)\s+_{4,}", traceback_text)
        if test_m:
            failing_test = test_m.group(1)
        else:
            test_m2 = re.search(r"::(test_[a-zA-Z0-9_]+)", traceback_text)
            if test_m2:
                failing_test = test_m2.group(1)

        # 2. Error and message
        err_m = re.search(r"(?:E\s+|Exception:\s+|Error:\s+)([A-Za-z0-9_]+Error|[A-Za-z0-9_]+Exception)(?::\s+(.*))?", traceback_text)
        if err_m:
            error_type = err_m.group(1)
            error_msg = (err_m.group(2) or "").strip()

        # 3. Assertion and values
        assert_m = re.search(r">\s*assert\s+(.*)", traceback_text)
        if not assert_m:
            assert_m = re.search(r"E\s+(?:[A-Za-z0-9_]+Error:\s+)?assert\s+(.*)", traceback_text)
        if assert_m:
            assertion_stmt = assert_m.group(1).strip()
            diff_m = re.search(r"([^\s=]+)\s*==\s*([^\s=]+)", assertion_stmt)
            if diff_m:
                actual = diff_m.group(1).strip()
                expected = diff_m.group(2).strip()

        # 4. Call stack frames
        frame_pattern = re.compile(r'File\s+"([^"]+)",\s+line\s+(\d+),\s+in\s+([a-zA-Z0-9_]+)')
        frames = frame_pattern.findall(traceback_text)
        for fpath, lno, func in frames:
            f_clean = fpath.replace("\\", "/").split("/")[-1]
            call_stack.append(f"{f_clean}:{func}:L{lno}")
            failing_file = fpath
            failing_line = int(lno)

        return StructuredFailureEvidence(
            failing_test=failing_test,
            failing_file=failing_file,
            failing_line=failing_line,
            error_type=error_type,
            error_message=error_msg,
            assertion_statement=assertion_stmt,
            expected_value=expected,
            actual_value=actual,
            call_stack=call_stack,
        )

    def analyze_candidate_state_flow(self, file_path: str, symbol: str) -> Optional[DeepStateFlowInfo]:
        """Runs deep StateFlowAnalyzer on a specific candidate method or function."""
        full_path = self.repo_dir / file_path
        if not full_path.exists():
            return None

        try:
            code = full_path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(code, filename=file_path)
        except Exception:
            return None

        clean_sym = symbol.split(".")[-1]
        target_ast = None
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == clean_sym:
                target_ast = node
                break

        if not target_ast:
            return None

        analyzer = StateFlowAnalyzer(target_ast)
        summary = analyzer.analyze()

        # Extract overwritten variables & conditional modifications
        assigned_counts: Dict[str, int] = {}
        for t in summary.transitions:
            if t.action in ("ASSIGN", "MUTATE", "LOOP_CARRY"):
                assigned_counts[t.variable] = assigned_counts.get(t.variable, 0) + 1

        overwritten = [var for var, count in assigned_counts.items() if count > 1]
        cond_modified = []
        for stmt in ast.walk(target_ast):
            if isinstance(stmt, ast.If):
                for sub in ast.walk(stmt):
                    if isinstance(sub, ast.Assign):
                        for tgt in sub.targets:
                            if isinstance(tgt, ast.Name):
                                cond_modified.append(tgt.id)

        key_transitions = [f"L{t.line}: [{t.action}] {t.variable} = {t.expression}" for t in summary.transitions if t.action in ("MUTATE", "LOOP_CARRY", "ASSIGN")][:10]

        return DeepStateFlowInfo(
            function_name=clean_sym,
            parameters=summary.parameters,
            overwritten_vars=sorted(set(overwritten)),
            mutated_attributes=sorted(summary.mutated_variables),
            conditionally_modified=sorted(set(cond_modified)),
            returned_vars=sorted(summary.returned_variables),
            loop_carried=sorted(summary.loop_carried_variables),
            key_transitions=key_transitions,
        )

    def extract_bounded_class_context(
        self, file_path: str, class_name: str, active_symbols: List[str]
    ) -> Optional[BoundedClassContext]:
        """Extracts bounded context for large classes: full source for active symbols, 1-line stubs for others."""
        full_path = self.repo_dir / file_path
        if not full_path.exists():
            return None

        try:
            code = full_path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(code, filename=file_path)
            lines = code.splitlines()
        except Exception:
            return None

        class_node = None
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == class_name:
                class_node = node
                break

        if not class_node:
            return None

        # Class header: class ClassName(...) + docstring
        header_lines = [lines[class_node.lineno - 1]]
        doc = ast.get_docstring(class_node)
        if doc:
            header_lines.append(f'    """{doc[:120]}..."""')
        class_header = "\n".join(header_lines)

        active_clean = {s.split(".")[-1] for s in active_symbols}
        relevant_src: Dict[str, str] = {}
        stubs: List[str] = []

        for item in class_node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                end_l = getattr(item, "end_lineno", item.lineno)
                if item.name in active_clean:
                    item_code = "\n".join(lines[item.lineno - 1 : end_l])
                    relevant_src[item.name] = item_code
                else:
                    # 1-line stub
                    params = [a.arg for a in item.args.args]
                    stubs.append(f"def {item.name}({', '.join(params[:5])}): ... (L{item.lineno}-L{end_l})")

        return BoundedClassContext(
            class_name=class_name,
            file_path=file_path,
            start_line=class_node.lineno,
            end_line=getattr(class_node, "end_lineno", class_node.lineno),
            class_header=class_header,
            relevant_methods_source=relevant_src,
            sibling_stubs=stubs,
        )

    def build_test_distinction(
        self, candidate_profiles: List[CandidateProfile]
    ) -> Dict[str, str]:
        """Identifies tests that distinguish one candidate from another."""
        distinctions: Dict[str, str] = {}
        if not self.test_index or len(candidate_profiles) < 2:
            return distinctions

        for i, c1 in enumerate(candidate_profiles):
            tests1 = set(c1.direct_tests)
            for c2 in candidate_profiles[i + 1 :]:
                tests2 = set(c2.direct_tests)
                unique1 = tests1 - tests2
                unique2 = tests2 - tests1
                if unique1:
                    distinctions[c1.symbol] = f"Tested uniquely by: {', '.join(list(unique1)[:3])} (does not touch {c2.symbol})"
                if unique2:
                    distinctions[c2.symbol] = f"Tested uniquely by: {', '.join(list(unique2)[:3])} (does not touch {c1.symbol})"

        return distinctions
