"""AST-grounded Repair Unit Planner for PatchForge AI.

Selects the minimal semantic blast radius (EXPRESSION, STATEMENT, STATEMENT_BLOCK, FUNCTION)
based on AST node hierarchy, verified source spans, and diagnostic invariants.
"""
from __future__ import annotations

import ast
import logging
from typing import Any, Dict, List, Optional, Tuple

from patchforge.repair.schema import RepairUnit, RepairUnitType
from patchforge.repository_intelligence.diagnosis import DiagnosisResult

logger = logging.getLogger(__name__)


class ASTSpanVisitor(ast.NodeVisitor):
    """Finds AST nodes matching or enclosing specified line spans."""

    def __init__(self, target_line_start: int, target_line_end: int):
        self.target_start = target_line_start
        self.target_end = target_line_end
        self.matching_statements: List[ast.stmt] = []
        self.matching_expressions: List[ast.expr] = []
        self.enclosing_function: Optional[ast.FunctionDef | ast.AsyncFunctionDef] = None
        self.enclosing_class: Optional[ast.ClassDef] = None
        self.current_function: Optional[ast.FunctionDef | ast.AsyncFunctionDef] = None
        self.current_class: Optional[ast.ClassDef] = None

    def visit_ClassDef(self, node: ast.ClassDef):
        prev_class = self.current_class
        self.current_class = node
        if node.lineno <= self.target_start and getattr(node, "end_lineno", node.lineno) >= self.target_end:
            self.enclosing_class = node
        self.generic_visit(node)
        self.current_class = prev_class

    def visit_FunctionDef(self, node: ast.FunctionDef):
        self._handle_func(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        self._handle_func(node)

    def _handle_func(self, node: ast.FunctionDef | ast.AsyncFunctionDef):
        prev_func = self.current_function
        self.current_function = node
        end_line = getattr(node, "end_lineno", node.lineno)
        if node.lineno <= self.target_start and end_line >= self.target_end:
            self.enclosing_function = node
        self.generic_visit(node)
        self.current_function = prev_func

    def generic_visit(self, node: ast.AST):
        if isinstance(node, ast.stmt):
            lineno = getattr(node, "lineno", None)
            end_lineno = getattr(node, "end_lineno", lineno)
            if lineno is not None and end_lineno is not None:
                # Check if statement overlaps or covers target range
                if not (end_lineno < self.target_start or lineno > self.target_end):
                    self.matching_statements.append(node)
        elif isinstance(node, ast.expr):
            lineno = getattr(node, "lineno", None)
            end_lineno = getattr(node, "end_lineno", lineno)
            if lineno is not None and end_lineno is not None:
                # Check if expression falls within target range
                if self.target_start <= lineno <= end_lineno <= self.target_end:
                    self.matching_expressions.append(node)

        super().generic_visit(node)


class RepairUnitPlanner:
    """Plans minimal-blast-radius AST repair units with verified source spans."""

    @staticmethod
    def plan_repair_unit(
        file_path: str,
        source_code: str,
        target_symbol: str = "",
        target_lines: Optional[Tuple[int, int]] = None,
        diagnosis: Optional[DiagnosisResult] = None,
        role: str = "PRIMARY",
        preferred_granularity: Optional[RepairUnitType] = None,
    ) -> RepairUnit:
        """Determines the optimal RepairUnit for a target within source code."""
        lines = source_code.splitlines(keepends=True)
        total_lines = len(lines)

        try:
            tree = ast.parse(source_code)
        except SyntaxError as e:
            logger.warning(f"Could not parse AST for {file_path}: {e}")
            # Fallback to line-range statement block
            start_l = target_lines[0] if target_lines else 1
            end_l = target_lines[1] if target_lines else min(total_lines, 50)
            return RepairUnit(
                id=f"{file_path}::{target_symbol}::STATEMENT_BLOCK::{start_l}-{end_l}",
                file_path=file_path,
                symbol=target_symbol,
                unit_type=RepairUnitType.STATEMENT_BLOCK,
                node_type="Unknown",
                start_line=start_l,
                end_line=end_l,
                source_text="".join(lines[start_l - 1:end_l]),
                role=role,
            )

        # If no target lines given, try to find target_symbol in AST
        if not target_lines:
            target_node = RepairUnitPlanner._find_symbol_node(tree, target_symbol)
            if target_node:
                target_lines = (target_node.lineno, getattr(target_node, "end_lineno", target_node.lineno))
            else:
                target_lines = (1, min(total_lines, 30))

        start_l, end_l = target_lines
        start_l = max(1, min(start_l, total_lines))
        end_l = max(start_l, min(end_l, total_lines))

        # Find enclosing & matching AST nodes
        visitor = ASTSpanVisitor(start_l, end_l)
        visitor.visit(tree)

        # 1. Check if an exact expression was requested or matched
        if preferred_granularity == RepairUnitType.EXPRESSION and visitor.matching_expressions:
            # Sort expressions preferring Call, BinOp, Compare, Attribute over simple Name/Constant
            def expr_priority(e):
                if isinstance(e, ast.Call):
                    return 10
                if isinstance(e, (ast.BinOp, ast.Compare, ast.BoolOp)):
                    return 8
                if isinstance(e, ast.Attribute):
                    return 6
                if isinstance(e, ast.Subscript):
                    return 5
                return 1

            visitor.matching_expressions.sort(key=expr_priority, reverse=True)
            expr_node = visitor.matching_expressions[0]
            seg = ast.get_source_segment(source_code, expr_node) or ""
            return RepairUnit(
                id=f"{file_path}::{target_symbol}::EXPRESSION::{expr_node.lineno}:{expr_node.col_offset}",
                file_path=file_path,
                symbol=target_symbol or (visitor.enclosing_function.name if visitor.enclosing_function else ""),
                unit_type=RepairUnitType.EXPRESSION,
                node_type=expr_node.__class__.__name__,
                start_line=expr_node.lineno,
                end_line=getattr(expr_node, "end_lineno", expr_node.lineno),
                start_col=expr_node.col_offset,
                end_col=getattr(expr_node, "end_col_offset", 0),
                source_text=seg,
                parent_symbol=visitor.enclosing_function.name if visitor.enclosing_function else "",
                role=role,
            )

        # 2. Check if a single statement matches
        tightest_stmt = None
        for stmt in visitor.matching_statements:
            s_start = stmt.lineno
            s_end = getattr(stmt, "end_lineno", s_start)
            # Find tightest covering statement
            if s_start <= start_l and s_end >= end_l:
                if tightest_stmt is None or (s_end - s_start) < (getattr(tightest_stmt, "end_lineno", tightest_stmt.lineno) - tightest_stmt.lineno):
                    tightest_stmt = stmt

        # Determine if statement, block, or whole function
        if tightest_stmt and not isinstance(tightest_stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            s_start = tightest_stmt.lineno
            s_end = getattr(tightest_stmt, "end_lineno", s_start)
            indent = RepairUnitPlanner._get_indentation(lines, s_start)
            source_seg = "".join(lines[s_start - 1:s_end])

            if s_start == s_end:
                u_type = RepairUnitType.STATEMENT
            else:
                u_type = RepairUnitType.STATEMENT_BLOCK

            if preferred_granularity:
                u_type = preferred_granularity

            return RepairUnit(
                id=f"{file_path}::{target_symbol}::{u_type.value}::{s_start}-{s_end}",
                file_path=file_path,
                symbol=target_symbol or (visitor.enclosing_function.name if visitor.enclosing_function else ""),
                unit_type=u_type,
                node_type=tightest_stmt.__class__.__name__,
                start_line=s_start,
                end_line=s_end,
                start_col=tightest_stmt.col_offset,
                end_col=getattr(tightest_stmt, "end_col_offset", len(lines[s_end - 1]) if s_end <= total_lines else 0),
                source_text=source_seg,
                parent_symbol=visitor.enclosing_function.name if visitor.enclosing_function else "",
                indentation=indent,
                role=role,
            )

        # 3. If enclosing function found, return FUNCTION / METHOD
        if visitor.enclosing_function:
            func = visitor.enclosing_function
            f_start = func.lineno
            f_end = getattr(func, "end_lineno", f_start)
            indent = RepairUnitPlanner._get_indentation(lines, f_start)
            source_seg = "".join(lines[f_start - 1:f_end])
            u_type = RepairUnitType.METHOD if visitor.enclosing_class else RepairUnitType.FUNCTION

            return RepairUnit(
                id=f"{file_path}::{func.name}::{u_type.value}::{f_start}-{f_end}",
                file_path=file_path,
                symbol=func.name,
                unit_type=u_type,
                node_type=func.__class__.__name__,
                start_line=f_start,
                end_line=f_end,
                start_col=func.col_offset,
                end_col=getattr(func, "end_col_offset", 0),
                source_text=source_seg,
                parent_symbol=visitor.enclosing_class.name if visitor.enclosing_class else "",
                indentation=indent,
                role=role,
            )

        # 4. Fallback: line slice
        indent = RepairUnitPlanner._get_indentation(lines, start_l)
        source_seg = "".join(lines[start_l - 1:end_l])
        return RepairUnit(
            id=f"{file_path}::{target_symbol}::STATEMENT_BLOCK::{start_l}-{end_l}",
            file_path=file_path,
            symbol=target_symbol,
            unit_type=RepairUnitType.STATEMENT_BLOCK,
            node_type="Block",
            start_line=start_l,
            end_line=end_l,
            source_text=source_seg,
            indentation=indent,
            role=role,
        )

    @staticmethod
    def _find_symbol_node(tree: ast.AST, symbol: str) -> Optional[ast.AST]:
        """Finds FunctionDef or ClassDef matching symbol name."""
        if not symbol:
            return None
        parts = symbol.split(".")
        target_name = parts[-1]

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if node.name == target_name:
                    return node
        return None

    @staticmethod
    def _get_indentation(lines: List[str], line_no: int) -> str:
        """Extracts the leading whitespace indentation of a line (1-indexed)."""
        if 1 <= line_no <= len(lines):
            line = lines[line_no - 1]
            return line[:len(line) - len(line.lstrip())]
        return ""

    @staticmethod
    def plan_multi_site_repair_units(
        sources: Dict[str, str],
        sites: List[Any],  # EditSite or dict or tuple
        diagnosis: Optional[Any] = None,
    ) -> List[RepairUnit]:
        """Plans non-overlapping coordinated AST repair units across multiple sites."""
        units: List[RepairUnit] = []
        seen_spans: Dict[str, List[Tuple[int, int]]] = {}

        for site in sites:
            file_path = getattr(site, "file_path", "") or (site.get("file_path", "") if isinstance(site, dict) else "")
            symbol = getattr(site, "symbol", "") or (site.get("symbol", "") if isinstance(site, dict) else "")
            l_start = getattr(site, "line_start", 1) if hasattr(site, "line_start") else (site.get("line_start", 1) if isinstance(site, dict) else 1)
            l_end = getattr(site, "line_end", 1) if hasattr(site, "line_end") else (site.get("line_end", 1) if isinstance(site, dict) else 1)
            role = getattr(site, "site_role", "PRIMARY") if hasattr(site, "site_role") else "PRIMARY"

            norm_file = file_path.replace("\\", "/")
            source_code = sources.get(norm_file, "")
            if not source_code:
                continue

            unit = RepairUnitPlanner.plan_repair_unit(
                file_path=norm_file,
                source_code=source_code,
                target_symbol=symbol,
                target_lines=(l_start, l_end),
                diagnosis=diagnosis,
                role=role,
            )

            # Check overlap against already planned units in the same file
            overlaps = False
            for span_s, span_e in seen_spans.get(norm_file, []):
                if max(unit.start_line, span_s) <= min(unit.end_line, span_e):
                    overlaps = True
                    break

            if not overlaps:
                units.append(unit)
                seen_spans.setdefault(norm_file, []).append((unit.start_line, unit.end_line))

        return units

