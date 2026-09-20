"""Bounded Control-Flow (CFG) and State-Flow Analyzer for PatchForge Repository Intelligence."""
import ast
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


@dataclass
class CFGBlock:
    """A bounded hierarchical control-flow block."""
    block_type: str  # function, if, elif, else, for, while, try, except, finally, return, raise, break, continue, stmt
    description: str
    start_line: int
    end_line: int
    children: List["CFGBlock"] = field(default_factory=list)

    def to_tree_str(self, indent: int = 0) -> str:
        prefix = "  " * indent
        branch = "\\-- " if indent > 0 else ""
        lines_info = f" (L{self.start_line}-L{self.end_line})" if self.start_line else ""
        res = [f"{prefix}{branch}{self.block_type}: {self.description}{lines_info}"]
        for child in self.children:
            res.append(child.to_tree_str(indent + 1))
        return "\n".join(res)



class ControlFlowBuilder(ast.NodeVisitor):
    """Builds a bounded control-flow tree from a function's AST."""

    def __init__(self):
        self.root: Optional[CFGBlock] = None
        self.current_parent: Optional[CFGBlock] = None

    def build(self, node: ast.AST) -> Optional[CFGBlock]:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return None
            
        end_line = getattr(node, "end_lineno", node.lineno)
        self.root = CFGBlock(
            block_type="function",
            description=node.name,
            start_line=node.lineno,
            end_line=end_line,
        )
        self.current_parent = self.root
        
        for stmt in node.body:
            self._process_stmt(stmt, self.root)
            
        return self.root

    def _process_stmt(self, stmt: ast.stmt, parent: CFGBlock):
        end_line = getattr(stmt, "end_lineno", stmt.lineno)
        
        if isinstance(stmt, ast.If):
            cond_str = ast.unparse(stmt.test)
            if_block = CFGBlock(block_type="if", description=cond_str, start_line=stmt.lineno, end_line=end_line)
            parent.children.append(if_block)
            for s in stmt.body:
                self._process_stmt(s, if_block)
            if stmt.orelse:
                else_block = CFGBlock(block_type="else", description="", start_line=stmt.orelse[0].lineno, end_line=end_line)
                parent.children.append(else_block)
                for s in stmt.orelse:
                    self._process_stmt(s, else_block)

        elif isinstance(stmt, (ast.For, ast.AsyncFor)):
            target_str = f"{ast.unparse(stmt.target)} in {ast.unparse(stmt.iter)}"
            for_block = CFGBlock(block_type="for", description=target_str, start_line=stmt.lineno, end_line=end_line)
            parent.children.append(for_block)
            for s in stmt.body:
                self._process_stmt(s, for_block)

        elif isinstance(stmt, ast.While):
            cond_str = ast.unparse(stmt.test)
            while_block = CFGBlock(block_type="while", description=cond_str, start_line=stmt.lineno, end_line=end_line)
            parent.children.append(while_block)
            for s in stmt.body:
                self._process_stmt(s, while_block)

        elif isinstance(stmt, ast.Try):
            try_block = CFGBlock(block_type="try", description="", start_line=stmt.lineno, end_line=end_line)
            parent.children.append(try_block)
            for s in stmt.body:
                self._process_stmt(s, try_block)
            for handler in stmt.handlers:
                exc_desc = ast.unparse(handler.type) if handler.type else "Exception"
                if handler.name:
                    exc_desc += f" as {handler.name}"
                h_end = getattr(handler, "end_lineno", handler.lineno)
                handler_block = CFGBlock(block_type="except", description=exc_desc, start_line=handler.lineno, end_line=h_end)
                parent.children.append(handler_block)
                for s in handler.body:
                    self._process_stmt(s, handler_block)
            if stmt.finalbody:
                fin_block = CFGBlock(block_type="finally", description="", start_line=stmt.finalbody[0].lineno, end_line=end_line)
                parent.children.append(fin_block)
                for s in stmt.finalbody:
                    self._process_stmt(s, fin_block)

        elif isinstance(stmt, ast.Return):
            val_str = ast.unparse(stmt.value) if stmt.value else "None"
            parent.children.append(CFGBlock(block_type="return", description=val_str, start_line=stmt.lineno, end_line=end_line))

        elif isinstance(stmt, ast.Raise):
            exc_str = ast.unparse(stmt.exc) if stmt.exc else ""
            parent.children.append(CFGBlock(block_type="raise", description=exc_str, start_line=stmt.lineno, end_line=end_line))

        elif isinstance(stmt, (ast.Break, ast.Continue)):
            b_type = "break" if isinstance(stmt, ast.Break) else "continue"
            parent.children.append(CFGBlock(block_type=b_type, description="", start_line=stmt.lineno, end_line=end_line))

        elif isinstance(stmt, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            assign_str = ast.unparse(stmt)
            if len(assign_str) > 60:
                assign_str = assign_str[:57] + "..."
            parent.children.append(CFGBlock(block_type="assign", description=assign_str, start_line=stmt.lineno, end_line=end_line))

        elif isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
            call_str = ast.unparse(stmt.value)
            if len(call_str) > 60:
                call_str = call_str[:57] + "..."
            parent.children.append(CFGBlock(block_type="call", description=call_str, start_line=stmt.lineno, end_line=end_line))


@dataclass
class VariableTransition:
    variable: str
    action: str  # PARAMETER, ASSIGN, MUTATE, RETURN, LOOP_CARRY
    line: int
    expression: str


@dataclass
class StateFlowSummary:
    function_name: str
    parameters: List[str]
    assigned_variables: Set[str]
    mutated_variables: Set[str]
    returned_variables: Set[str]
    loop_carried_variables: Set[str]
    transitions: List[VariableTransition]

    def format_summary(self) -> str:
        lines = [f"State Flow for '{self.function_name}':"]
        lines.append(f"  - Parameters: {', '.join(self.parameters) if self.parameters else 'None'}")
        if self.loop_carried_variables:
            lines.append(f"  - Loop-Carried State (Critical): {', '.join(sorted(self.loop_carried_variables))}")
        if self.mutated_variables:
            lines.append(f"  - Mutated Attributes/Containers: {', '.join(sorted(self.mutated_variables))}")
        if self.returned_variables:
            lines.append(f"  - Returned Values: {', '.join(sorted(self.returned_variables))}")
        
        lines.append("  - Key Transitions:")
        for t in self.transitions[:12]:
            lines.append(f"      L{t.line}: [{t.action}] {t.variable} = {t.expression}")
        return "\n".join(lines)


class StateFlowAnalyzer(ast.NodeVisitor):
    """Tracks variable lifecycle, assignments, mutations, and loop-carried state within a function."""

    def __init__(self, func_ast: Any):
        self.func_ast = func_ast
        self.function_name = getattr(func_ast, "name", "anonymous")
        self.parameters: List[str] = [a.arg for a in getattr(func_ast.args, "args", [])]
        self.assigned: Set[str] = set()
        self.mutated: Set[str] = set()
        self.returned: Set[str] = set()
        self.loop_carried: Set[str] = set()
        self.transitions: List[VariableTransition] = []
        
        # Track loop depth
        self.loop_depth = 0
        self.vars_assigned_in_loop: Set[str] = set()

    def analyze(self) -> StateFlowSummary:
        # Record parameters
        for p in self.parameters:
            self.transitions.append(
                VariableTransition(variable=p, action="PARAMETER", line=getattr(self.func_ast, "lineno", 0), expression=p)
            )
            
        self.visit(self.func_ast)
        
        return StateFlowSummary(
            function_name=self.function_name,
            parameters=self.parameters,
            assigned_variables=self.assigned,
            mutated_variables=self.mutated,
            returned_variables=self.returned,
            loop_carried_variables=self.loop_carried,
            transitions=self.transitions,
        )

    def visit_Assign(self, node: ast.Assign):
        val_str = ast.unparse(node.value)
        for target in node.targets:
            if isinstance(target, ast.Name):
                var_name = target.id
                self.assigned.add(var_name)
                action = "ASSIGN"
                if self.loop_depth > 0:
                    self.loop_carried.add(var_name)
                    action = "LOOP_CARRY"
                self.transitions.append(
                    VariableTransition(variable=var_name, action=action, line=node.lineno, expression=val_str[:50])
                )
            elif isinstance(target, ast.Attribute):
                attr_str = ast.unparse(target)
                self.mutated.add(attr_str)
                self.transitions.append(
                    VariableTransition(variable=attr_str, action="MUTATE", line=node.lineno, expression=val_str[:50])
                )
            elif isinstance(target, ast.Subscript):
                sub_str = ast.unparse(target)
                self.mutated.add(sub_str)
                self.transitions.append(
                    VariableTransition(variable=sub_str, action="MUTATE", line=node.lineno, expression=val_str[:50])
                )
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign):
        target_str = ast.unparse(node.target)
        val_str = ast.unparse(node.value)
        self.mutated.add(target_str)
        action = "MUTATE"
        if self.loop_depth > 0 and isinstance(node.target, ast.Name):
            self.loop_carried.add(node.target.id)
            action = "LOOP_CARRY"
        self.transitions.append(
            VariableTransition(variable=target_str, action=action, line=node.lineno, expression=f"+= {val_str[:40]}")
        )
        self.generic_visit(node)

    def visit_For(self, node: ast.For):
        self.loop_depth += 1
        self.generic_visit(node)
        self.loop_depth -= 1

    def visit_While(self, node: ast.While):
        self.loop_depth += 1
        self.generic_visit(node)
        self.loop_depth -= 1

    def visit_Return(self, node: ast.Return):
        if node.value:
            ret_str = ast.unparse(node.value)
            self.returned.add(ret_str)
            self.transitions.append(
                VariableTransition(variable="<return>", action="RETURN", line=node.lineno, expression=ret_str[:50])
            )
        self.generic_visit(node)


def analyze_symbol_flow(source_code: str, symbol_name: str) -> Tuple[Optional[str], Optional[str]]:
    """Convenience helper: returns (cfg_tree_str, state_flow_str) for a function in source code."""
    try:
        tree = ast.parse(source_code)
    except Exception as e:
        logger.warning(f"Failed to parse source for flow analysis: {e}")
        return None, None

    target_node = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == symbol_name:
                target_node = node
                break

    if not target_node:
        return None, None

    cfg_builder = ControlFlowBuilder()
    cfg_root = cfg_builder.build(target_node)
    cfg_str = cfg_root.to_tree_str() if cfg_root else None

    state_analyzer = StateFlowAnalyzer(target_node)
    state_summary = state_analyzer.analyze()
    state_str = state_summary.format_summary()

    return cfg_str, state_str
