"""Deterministic State-Flow and Control-Flow Analyzer.

Extracts AST-based structural flow diagnostics:
- Variable definitions, assignments, and scoping
- Explicit copies (e.g. .copy(), copy.copy(), list(), dict())
- In-place mutations (attribute and subscript assignments)
- Loop structures and loop-carried dependencies
- Exception handling blocks (try/except/finally)
- Branch points and early returns
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Any


@dataclass
class LoopSummary:
    loop_type: str  # "for" or "while"
    lineno: int
    loop_var: str
    carried_vars: list[str] = field(default_factory=list)
    copies_inside: list[str] = field(default_factory=list)
    mutations_inside: list[str] = field(default_factory=list)
    has_break: bool = False
    has_continue: bool = False
    has_return: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.loop_type,
            "lineno": self.lineno,
            "loop_var": self.loop_var,
            "carried_vars": self.carried_vars,
            "copies_inside": self.copies_inside,
            "mutations_inside": self.mutations_inside,
            "has_break": self.has_break,
            "has_continue": self.has_continue,
            "has_return": self.has_return,
        }


@dataclass
class ExceptionBlockSummary:
    lineno: int
    caught_exceptions: list[str] = field(default_factory=list)
    has_reraise: bool = False
    is_suppressed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "lineno": self.lineno,
            "caught": self.caught_exceptions,
            "has_reraise": self.has_reraise,
            "is_suppressed": self.is_suppressed,
        }


@dataclass
class StateFlowReport:
    symbol_name: str
    lineno: int
    parameters: list[str] = field(default_factory=list)
    assigned_vars: list[str] = field(default_factory=list)
    copied_vars: list[str] = field(default_factory=list)
    attribute_mutations: list[str] = field(default_factory=list)
    subscript_mutations: list[str] = field(default_factory=list)
    loops: list[LoopSummary] = field(default_factory=list)
    exception_blocks: list[ExceptionBlockSummary] = field(default_factory=list)
    assertions: list[str] = field(default_factory=list)
    return_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol_name,
            "lineno": self.lineno,
            "parameters": self.parameters,
            "assigned_vars": self.assigned_vars,
            "copied_vars": self.copied_vars,
            "attribute_mutations": self.attribute_mutations,
            "subscript_mutations": self.subscript_mutations,
            "loops": [l.to_dict() for l in self.loops],
            "exception_blocks": [e.to_dict() for e in self.exception_blocks],
            "assertions": self.assertions,
            "return_count": self.return_count,
        }

    def format_summary(self) -> str:
        lines: list[str] = []
        lines.append(f"=== STATE & CONTROL FLOW DIAGNOSTICS ===")
        lines.append(f"Target: {self.symbol_name} (line {self.lineno})")
        if self.parameters:
            lines.append(f"Parameters: {', '.join(self.parameters)}")
        if self.assertions:
            lines.append("Internal Assertions in verified source:")
            for a in self.assertions[:4]:
                lines.append(f"  - {a}")
        if self.copied_vars:
            lines.append(f"Explicit Copies: {', '.join(self.copied_vars)}")
        if self.attribute_mutations:
            lines.append(f"Attribute Mutations: {', '.join(sorted(set(self.attribute_mutations))[:8])}")
        if self.subscript_mutations:
            lines.append(f"Subscript Mutations: {', '.join(sorted(set(self.subscript_mutations))[:8])}")

        for l in self.loops:
            var_desc = f" ({l.loop_var})" if l.loop_var else ""
            lines.append(f"Loop ({l.loop_type}{var_desc}, line {l.lineno}):")
            if l.carried_vars:
                lines.append(f"  - Loop-carried variables modified: {', '.join(sorted(set(l.carried_vars)))}")
            if l.copies_inside:
                lines.append(f"  - Copies created in loop: {', '.join(sorted(set(l.copies_inside)))}")
            if l.mutations_inside:
                lines.append(f"  - Mutations in loop: {', '.join(sorted(set(l.mutations_inside))[:5])}")
            flow_flags = []
            if l.has_break:
                flow_flags.append("break")
            if l.has_continue:
                flow_flags.append("continue")
            if l.has_return:
                flow_flags.append("return")
            if flow_flags:
                lines.append(f"  - Control jumps: {', '.join(flow_flags)}")

        for e in self.exception_blocks:
            caught = ", ".join(e.caught_exceptions) if e.caught_exceptions else "Exception"
            status = "re-raised" if e.has_reraise else ("suppressed" if e.is_suppressed else "handled")
            lines.append(f"Exception Handler (line {e.lineno}): catches [{caught}] -> {status}")

        return "\n".join(lines)


class StateFlowAnalyzer(ast.NodeVisitor):
    def __init__(self, target_symbol: str = ""):
        self.target_symbol = target_symbol
        self.reports: list[StateFlowReport] = []
        self.current_report: StateFlowReport | None = None
        self._defined_before_loop: set[str] = set()

    def analyze_source(self, source_code: str) -> list[StateFlowReport]:
        try:
            tree = ast.parse(source_code)
            self.visit(tree)
        except SyntaxError:
            pass
        return self.reports

    def visit_FunctionDef(self, node: ast.FunctionDef) -> Any:
        self._process_func(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> Any:
        self._process_func(node)

    def _process_func(self, node: ast.FunctionDef | ast.AsyncFunctionDef):
        # If target_symbol specified, only analyze matching function
        if self.target_symbol and node.name != self.target_symbol:
            self.generic_visit(node)
            return

        params = [arg.arg for arg in node.args.args]
        if node.args.vararg:
            params.append(f"*{node.args.vararg.arg}")
        if node.args.kwarg:
            params.append(f"**{node.args.kwarg.arg}")

        report = StateFlowReport(
            symbol_name=node.name,
            lineno=node.lineno,
            parameters=params,
        )
        prev_report = self.current_report
        self.current_report = report
        self._defined_before_loop = set(params)

        # Walk function body
        self._walk_function_body(node.body, report)

        self.reports.append(report)
        self.current_report = prev_report

    def _walk_function_body(self, statements: list[ast.stmt], report: StateFlowReport):
        for stmt in statements:
            self._inspect_statement(stmt, report)

    def _inspect_statement(self, stmt: ast.stmt, report: StateFlowReport):
        if isinstance(stmt, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            self._inspect_assignment(stmt, report)
        elif isinstance(stmt, (ast.For, ast.AsyncFor)):
            self._inspect_for_loop(stmt, report)
        elif isinstance(stmt, ast.While):
            self._inspect_while_loop(stmt, report)
        elif isinstance(stmt, ast.Try):
            self._inspect_try(stmt, report)
        elif isinstance(stmt, (ast.If)):
            for s in stmt.body:
                self._inspect_statement(s, report)
            for s in stmt.orelse:
                self._inspect_statement(s, report)
        elif isinstance(stmt, ast.Return):
            report.return_count += 1
        elif isinstance(stmt, ast.Assert):
            report.assertions.append(self._format_ast(stmt))
        elif isinstance(stmt, ast.Expr):
            # Check for mutating method calls like list.append, dict.pop
            self._inspect_expr(stmt.value, report)

    def _inspect_assignment(self, node: ast.Assign | ast.AugAssign | ast.AnnAssign, report: StateFlowReport):
        targets: list[ast.AST] = []
        if isinstance(node, ast.Assign):
            targets = node.targets
            value = node.value
        elif isinstance(node, ast.AugAssign):
            targets = [node.target]
            value = node.value
        else:
            targets = [node.target]
            value = node.value

        for tgt in targets:
            if isinstance(tgt, ast.Name):
                report.assigned_vars.append(tgt.id)
                self._defined_before_loop.add(tgt.id)
            elif isinstance(tgt, ast.Attribute):
                attr_str = self._format_ast(tgt)
                report.attribute_mutations.append(attr_str)
            elif isinstance(tgt, ast.Subscript):
                sub_str = self._format_ast(tgt)
                report.subscript_mutations.append(sub_str)

        # Check if right-hand side is a copy operation
        if value:
            self._check_for_copies(value, report)

    def _check_for_copies(self, expr: ast.AST, report: StateFlowReport):
        if isinstance(expr, ast.Call):
            func_name = self._format_ast(expr.func)
            if func_name.endswith(".copy") or func_name in ("copy.copy", "copy.deepcopy", "dict", "list", "set"):
                target_str = self._format_ast(expr)
                report.copied_vars.append(target_str)

    def _inspect_expr(self, expr: ast.AST, report: StateFlowReport):
        if isinstance(expr, ast.Call):
            func_str = self._format_ast(expr.func)
            if any(func_str.endswith(f".{m}") for m in ("append", "extend", "pop", "remove", "update", "clear")):
                report.attribute_mutations.append(func_str)

    def _inspect_for_loop(self, node: ast.For | ast.AsyncFor, report: StateFlowReport):
        loop_var = self._format_ast(node.target)
        loop = LoopSummary(loop_type="for", lineno=node.lineno, loop_var=loop_var)
        self._analyze_loop_body(node.body, loop, report)
        report.loops.append(loop)
        self._walk_function_body(node.body, report)
        if node.orelse:
            self._walk_function_body(node.orelse, report)

    def _inspect_while_loop(self, node: ast.While, report: StateFlowReport):
        test_str = self._format_ast(node.test)
        loop = LoopSummary(loop_type="while", lineno=node.lineno, loop_var=test_str)
        self._analyze_loop_body(node.body, loop, report)
        report.loops.append(loop)
        self._walk_function_body(node.body, report)
        if node.orelse:
            self._walk_function_body(node.orelse, report)

    def _analyze_loop_body(self, body: list[ast.stmt], loop: LoopSummary, report: StateFlowReport):
        for node in ast.walk(ast.Module(body=body, type_ignores=[])):
            if isinstance(node, ast.Break):
                loop.has_break = True
            elif isinstance(node, ast.Continue):
                loop.has_continue = True
            elif isinstance(node, ast.Return):
                loop.has_return = True
                report.return_count += 1
            elif isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for tgt in targets:
                    if isinstance(tgt, ast.Name):
                        if tgt.id in self._defined_before_loop:
                            loop.carried_vars.append(tgt.id)
                        report.assigned_vars.append(tgt.id)
                    elif isinstance(tgt, ast.Attribute):
                        loop.mutations_inside.append(self._format_ast(tgt))
                        report.attribute_mutations.append(self._format_ast(tgt))
                    elif isinstance(tgt, ast.Subscript):
                        loop.mutations_inside.append(self._format_ast(tgt))
                        report.subscript_mutations.append(self._format_ast(tgt))

                val = getattr(node, "value", None)
                if val and isinstance(val, ast.Call):
                    fn = self._format_ast(val.func)
                    if fn.endswith(".copy") or fn in ("copy.copy", "copy.deepcopy", "dict", "list", "set"):
                        loop.copies_inside.append(self._format_ast(val))
                        report.copied_vars.append(self._format_ast(val))

    def _inspect_try(self, node: ast.Try, report: StateFlowReport):
        caught = []
        reraise = False
        is_suppressed = False

        for h in node.handlers:
            if h.type:
                caught.append(self._format_ast(h.type))
            else:
                caught.append("Exception")
            for sub in h.body:
                if isinstance(sub, ast.Raise):
                    reraise = True
                elif isinstance(sub, ast.Pass) and len(h.body) == 1:
                    is_suppressed = True

        report.exception_blocks.append(
            ExceptionBlockSummary(
                lineno=node.lineno,
                caught_exceptions=caught,
                has_reraise=reraise,
                is_suppressed=is_suppressed,
            )
        )
        for s in node.body:
            self._inspect_statement(s, report)
        for h in node.handlers:
            for s in h.body:
                self._inspect_statement(s, report)

    def _format_ast(self, node: ast.AST) -> str:
        try:
            return ast.unparse(node)
        except Exception:
            return ""


def extract_state_flow(source_code: str, target_symbol: str = "") -> list[StateFlowReport]:
    """Analyze source code and return state flow reports."""
    analyzer = StateFlowAnalyzer(target_symbol=target_symbol)
    return analyzer.analyze_source(source_code)


def format_state_flow_summary(source_code: str, target_symbol: str = "") -> str:
    """Generate compact formatted structural diagnostics for LLM repair context."""
    reports = extract_state_flow(source_code, target_symbol=target_symbol)
    if not reports:
        return ""
    # Return the first matching report or primary report
    rep = reports[0]
    return rep.format_summary()
