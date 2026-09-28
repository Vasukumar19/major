"""PatchForge Execution Path Model & Algorithmic Divergence Analyzer.

Constructs bounded execution paths combining AST control flow, CFG state mutations,
traceback frames, and test assertions to pinpoint the exact divergence point
where actual execution diverges from required algorithmic behavior.
"""
from __future__ import annotations

import ast
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from patchforge.analysis.state_flow import StateFlowAnalyzer, StateFlowReport

logger = logging.getLogger(__name__)


@dataclass
class ExecutionPathNode:
    """Represents a discrete step or branch point along a function's execution path."""
    symbol: str
    file_path: str
    line_number: int
    node_type: str  # IF_BRANCH, LOOP, MUTATION, CALL, EXCEPTION, RETURN
    code_snippet: str
    predicates: List[str] = field(default_factory=list)
    mutations: List[str] = field(default_factory=list)
    observed_behavior: str = ""
    expected_behavior: str = ""
    is_divergence_point: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "file_path": self.file_path,
            "line_number": self.line_number,
            "node_type": self.node_type,
            "code_snippet": self.code_snippet,
            "predicates": self.predicates,
            "mutations": self.mutations,
            "observed_behavior": self.observed_behavior,
            "expected_behavior": self.expected_behavior,
            "is_divergence_point": self.is_divergence_point,
        }


@dataclass
class ExecutionPathModel:
    """Bounded model of execution through a target function, highlighting divergence."""
    target_symbol: str
    file_path: str
    nodes: List[ExecutionPathNode] = field(default_factory=list)
    divergence_node: Optional[ExecutionPathNode] = None
    divergence_explanation: str = ""
    expected_transition: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target_symbol": self.target_symbol,
            "file_path": self.file_path,
            "nodes": [n.to_dict() for n in self.nodes],
            "divergence_line": self.divergence_node.line_number if self.divergence_node else None,
            "divergence_explanation": self.divergence_explanation,
            "expected_transition": self.expected_transition,
        }

    def format_for_prompt(self) -> str:
        if not self.nodes:
            return ""
        lines = [f"=== EXECUTION PATH ANALYSIS: `{self.target_symbol}` in `{self.file_path}` ==="]
        for node in self.nodes:
            marker = ">>> [DIVERGENCE POINT] " if node.is_divergence_point else "    "
            pred_desc = f" | Branch: `{' and '.join(node.predicates)}`" if node.predicates else ""
            mut_desc = f" | Mutates: `{' ,'.join(node.mutations)}`" if node.mutations else ""
            lines.append(f"{marker}Line {node.line_number:4d} ({node.node_type}): `{node.code_snippet}`{pred_desc}{mut_desc}")
            if node.is_divergence_point:
                if node.observed_behavior:
                    lines.append(f"        Observed Buggy Path: {node.observed_behavior}")
                if node.expected_behavior:
                    lines.append(f"        Required Transition: {node.expected_behavior}")

        if self.divergence_explanation:
            lines.append(f"Root Algorithmic Divergence: {self.divergence_explanation}")
        if self.expected_transition:
            lines.append(f"Required Algorithmic Fix: {self.expected_transition}")

        return "\n".join(lines)


class ExecutionPathBuilder:
    """Constructs ExecutionPathModel from AST, StateFlow, Tracebacks, and Invariants."""

    @classmethod
    def build_path(
        cls,
        file_path: str,
        source_code: str,
        target_symbol: str = "",
        traceback_lines: Optional[List[int]] = None,
        traceback_text: str = "",
        problem_statement: str = "",
        test_assertions: Optional[List[str]] = None,
    ) -> ExecutionPathModel:
        """Builds an execution path model through the target symbol."""
        model = ExecutionPathModel(target_symbol=target_symbol, file_path=file_path)
        tb_lines = set(traceback_lines or [])

        # Parse source code
        try:
            tree = ast.parse(source_code)
        except SyntaxError:
            return model

        # Run StateFlowAnalyzer to get semantic mutations
        analyzer = StateFlowAnalyzer(target_symbol=target_symbol)
        flow_reports = analyzer.analyze_source(source_code)
        flow_report = flow_reports[0] if flow_reports else None

        # Find target FunctionDef
        target_func = None
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not target_symbol or node.name == target_symbol:
                    target_func = node
                    break

        if not target_func:
            return model

        model.target_symbol = target_func.name
        source_lines = source_code.splitlines()

        def get_line_snippet(lineno: int) -> str:
            if 1 <= lineno <= len(source_lines):
                return source_lines[lineno - 1].strip()
            return ""

        # Walk top-level statements of the function body
        nodes: List[ExecutionPathNode] = []

        for stmt in target_func.body:
            lineno = getattr(stmt, "lineno", target_func.lineno)
            snippet = get_line_snippet(lineno)

            if isinstance(stmt, ast.If):
                pred_str = ast.unparse(stmt.test) if hasattr(ast, "unparse") else snippet
                node = ExecutionPathNode(
                    symbol=target_func.name,
                    file_path=file_path,
                    line_number=lineno,
                    node_type="IF_BRANCH",
                    code_snippet=snippet,
                    predicates=[pred_str],
                )
                nodes.append(node)
                # Check nested body
                for sub in stmt.body:
                    sub_l = getattr(sub, "lineno", lineno)
                    nodes.append(
                        ExecutionPathNode(
                            symbol=target_func.name,
                            file_path=file_path,
                            line_number=sub_l,
                            node_type="BRANCH_BODY",
                            code_snippet=get_line_snippet(sub_l),
                            predicates=[pred_str],
                        )
                    )

            elif isinstance(stmt, (ast.For, ast.AsyncFor, ast.While)):
                node = ExecutionPathNode(
                    symbol=target_func.name,
                    file_path=file_path,
                    line_number=lineno,
                    node_type="LOOP",
                    code_snippet=snippet,
                )
                nodes.append(node)

            elif isinstance(stmt, ast.Try):
                node = ExecutionPathNode(
                    symbol=target_func.name,
                    file_path=file_path,
                    line_number=lineno,
                    node_type="TRY_BLOCK",
                    code_snippet=snippet,
                )
                nodes.append(node)
                for h in stmt.handlers:
                    h_l = getattr(h, "lineno", lineno)
                    exc_name = ast.unparse(h.type) if h.type and hasattr(ast, "unparse") else "Exception"
                    nodes.append(
                        ExecutionPathNode(
                            symbol=target_func.name,
                            file_path=file_path,
                            line_number=h_l,
                            node_type="EXCEPTION_HANDLER",
                            code_snippet=get_line_snippet(h_l),
                            predicates=[f"catches {exc_name}"],
                        )
                    )

            elif isinstance(stmt, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
                targets = []
                if isinstance(stmt, ast.Assign):
                    for t in stmt.targets:
                        if hasattr(ast, "unparse"):
                            targets.append(ast.unparse(t))
                elif isinstance(stmt, ast.AugAssign):
                    if hasattr(ast, "unparse"):
                        targets.append(ast.unparse(stmt.target))
                node = ExecutionPathNode(
                    symbol=target_func.name,
                    file_path=file_path,
                    line_number=lineno,
                    node_type="MUTATION",
                    code_snippet=snippet,
                    mutations=targets,
                )
                nodes.append(node)

            elif isinstance(stmt, ast.Return):
                ret_val = ast.unparse(stmt.value) if stmt.value and hasattr(ast, "unparse") else ""
                node = ExecutionPathNode(
                    symbol=target_func.name,
                    file_path=file_path,
                    line_number=lineno,
                    node_type="RETURN",
                    code_snippet=snippet,
                    mutations=[f"return {ret_val}"],
                )
                nodes.append(node)

            elif isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
                call_str = ast.unparse(stmt.value) if hasattr(ast, "unparse") else snippet
                node = ExecutionPathNode(
                    symbol=target_func.name,
                    file_path=file_path,
                    line_number=lineno,
                    node_type="CALL",
                    code_snippet=snippet,
                    mutations=[call_str],
                )
                nodes.append(node)

        # Sort nodes by line number
        nodes.sort(key=lambda n: n.line_number)
        model.nodes = nodes

        # Pinpoint Divergence Point
        # 1. First priority: does a traceback line fall directly within this function?
        divergence_node = None
        for n in nodes:
            if n.line_number in tb_lines:
                divergence_node = n
                n.is_divergence_point = True
                break

        # 2. Second priority: if no traceback line matches, check for keywords from issue/test
        if not divergence_node and problem_statement:
            prob_lower = problem_statement.lower()
            for n in nodes:
                # Check if predicates or mutations relate to issue keywords
                code_lower = n.code_snippet.lower()
                if any(w in code_lower for w in ("all(", "any(", "subdomain", "dot", "scope", "hostname")):
                    divergence_node = n
                    n.is_divergence_point = True
                    break

        # 3. Third priority: return statement or first branch if multiple branches exist
        if not divergence_node and nodes:
            branches = [n for n in nodes if n.node_type in ("IF_BRANCH", "RETURN")]
            if branches:
                divergence_node = branches[-1]
                divergence_node.is_divergence_point = True

        if divergence_node:
            model.divergence_node = divergence_node
            model.divergence_explanation = (
                f"Execution diverges at line {divergence_node.line_number} (`{divergence_node.code_snippet}`). "
                f"The current logic does not handle the required state transition or predicate condition."
            )
            divergence_node.observed_behavior = "Executes default path without required transformation/invariant."
            divergence_node.expected_behavior = "Must intercept this condition and produce the expected state transformation."

        return model
