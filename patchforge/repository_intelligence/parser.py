"""AST and Tree-Sitter semantic parser for PatchForge Repository Intelligence."""
import ast
import inspect
import logging
import os
from typing import Any, Dict, List, Optional, Set, Tuple

from patchforge.repository_intelligence.schema import (
    EdgeKind,
    GraphEdge,
    GraphNode,
    NodeKind,
)

logger = logging.getLogger(__name__)


class SymbolExtractor(ast.NodeVisitor):
    """Walks Python AST to extract nodes and relationships with precise line ranges."""

    def __init__(self, file_path: str, source_code: str):
        self.file_path = file_path.replace("\\", "/")
        self.source_code = source_code
        self.lines = source_code.splitlines()
        self.nodes: Dict[str, GraphNode] = {}
        self.edges: List[GraphEdge] = []
        
        # Scopes: list of parent symbol IDs
        self.scope_stack: List[str] = []
        self.class_stack: List[str] = []
        
        # Current module node
        self.module_id = f"module:{self.file_path}"
        self.nodes[self.module_id] = GraphNode(
            id=self.module_id,
            name=os.path.basename(self.file_path),
            kind=NodeKind.MODULE,
            file_path=self.file_path,
            start_line=1,
            end_line=len(self.lines),
            docstring=ast.get_docstring(ast.parse(source_code)) if source_code.strip() else None,
        )
        self.scope_stack.append(self.module_id)

    def _get_current_scope_id(self) -> str:
        return self.scope_stack[-1] if self.scope_stack else self.module_id

    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            target_id = f"module:{alias.name}"
            self.edges.append(
                GraphEdge(
                    source_id=self.module_id,
                    target_id=target_id,
                    kind=EdgeKind.IMPORTS,
                    attributes={"asname": alias.asname, "lineno": node.lineno},
                )
            )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        mod = node.module or ""
        for alias in node.names:
            target_id = f"symbol:{mod}.{alias.name}" if mod else f"symbol:{alias.name}"
            self.edges.append(
                GraphEdge(
                    source_id=self.module_id,
                    target_id=target_id,
                    kind=EdgeKind.IMPORTS,
                    attributes={"module": mod, "asname": alias.asname, "lineno": node.lineno},
                )
            )
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef):
        parent_scope = self._get_current_scope_id()
        is_test = node.name.startswith("Test") or "Test" in node.name or "test" in self.file_path
        kind = NodeKind.TEST_CLASS if (is_test and node.name.startswith("Test")) else NodeKind.CLASS
        
        # Unique node ID
        class_id = f"{self.file_path}:{node.name}"
        end_line = getattr(node, "end_lineno", node.lineno)
        
        docstring = ast.get_docstring(node)
        bases = [ast.unparse(b) for b in node.bases]
        
        class_node = GraphNode(
            id=class_id,
            name=node.name,
            kind=kind,
            file_path=self.file_path,
            start_line=node.lineno,
            end_line=end_line,
            docstring=docstring,
            signature=f"class {node.name}({', '.join(bases)})",
            attributes={"bases": bases, "decorators": [ast.unparse(d) for d in node.decorator_list]},
        )
        self.nodes[class_id] = class_node
        
        # DEFINES edge from parent scope
        self.edges.append(
            GraphEdge(source_id=parent_scope, target_id=class_id, kind=EdgeKind.DEFINES)
        )
        
        # INHERITS edges
        for base_name in bases:
            self.edges.append(
                GraphEdge(
                    source_id=class_id,
                    target_id=f"symbol:{base_name}",
                    kind=EdgeKind.INHERITS,
                )
            )
            
        # Push scope
        self.scope_stack.append(class_id)
        self.class_stack.append(class_id)
        
        self.generic_visit(node)
        
        self.class_stack.pop()
        self.scope_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef):
        self._handle_function(node, is_async=False)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        self._handle_function(node, is_async=True)

    def _handle_function(self, node: Any, is_async: bool):
        parent_scope = self._get_current_scope_id()
        is_method = bool(self.class_stack)
        is_test = (
            node.name.startswith("test_")
            or node.name.endswith("_test")
            or "test" in self.file_path.lower() and node.name.startswith("test")
        )
        
        if is_test:
            kind = NodeKind.TEST_FUNCTION
        elif is_method:
            kind = NodeKind.METHOD
        else:
            kind = NodeKind.FUNCTION
            
        # Node ID
        if is_method:
            func_id = f"{parent_scope}.{node.name}"
        else:
            func_id = f"{self.file_path}:{node.name}"
            
        end_line = getattr(node, "end_lineno", node.lineno)
        docstring = ast.get_docstring(node)
        
        # Format signature
        args_list = []
        for a in node.args.args:
            arg_str = a.arg
            if a.annotation:
                arg_str += f": {ast.unparse(a.annotation)}"
            args_list.append(arg_str)
        ret_ann = f" -> {ast.unparse(node.returns)}" if node.returns else ""
        prefix = "async def " if is_async else "def "
        sig = f"{prefix}{node.name}({', '.join(args_list)}){ret_ann}"
        
        func_node = GraphNode(
            id=func_id,
            name=node.name,
            kind=kind,
            file_path=self.file_path,
            start_line=node.lineno,
            end_line=end_line,
            docstring=docstring,
            signature=sig,
            attributes={
                "args": [a.arg for a in node.args.args],
                "decorators": [ast.unparse(d) for d in node.decorator_list],
                "is_async": is_async,
                "is_method": is_method,
                "parent_class": self.class_stack[-1] if self.class_stack else None,
            },
        )
        self.nodes[func_id] = func_node
        
        # DEFINES edge
        self.edges.append(
            GraphEdge(source_id=parent_scope, target_id=func_id, kind=EdgeKind.DEFINES)
        )
        
        # DECORATES edges
        for d in node.decorator_list:
            dec_name = ast.unparse(d)
            self.edges.append(
                GraphEdge(source_id=f"symbol:{dec_name}", target_id=func_id, kind=EdgeKind.DECORATES)
            )
            
        # Scope push
        self.scope_stack.append(func_id)
        
        # Inspect statements inside function for calls, raises, catches, asserts
        self._analyze_function_body(func_id, node)
        
        self.generic_visit(node)
        self.scope_stack.pop()

    def _analyze_function_body(self, func_id: str, func_ast: Any):
        """Extract CALLS, RAISES, CATCHES, ASSERTIONS, and REFERENCES inside function body."""
        for child in ast.walk(func_ast):
            if child is func_ast:
                continue
                
            # Avoid walking into nested functions/classes when attributing to current function
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue

            # CALLS
            if isinstance(child, ast.Call):
                call_name = ""
                if isinstance(child.func, ast.Name):
                    call_name = child.func.id
                elif isinstance(child.func, ast.Attribute):
                    call_name = child.func.attr
                if call_name:
                    self.edges.append(
                        GraphEdge(
                            source_id=func_id,
                            target_id=f"symbol:{call_name}",
                            kind=EdgeKind.CALLS,
                            attributes={"lineno": child.lineno},
                        )
                    )

            # RAISES
            elif isinstance(child, ast.Raise):
                exc_name = ""
                if child.exc:
                    if isinstance(child.exc, ast.Call) and isinstance(child.exc.func, (ast.Name, ast.Attribute)):
                        exc_name = child.exc.func.id if isinstance(child.exc.func, ast.Name) else child.exc.func.attr
                    elif isinstance(child.exc, ast.Name):
                        exc_name = child.exc.id
                if exc_name:
                    self.edges.append(
                        GraphEdge(
                            source_id=func_id,
                            target_id=f"exception:{exc_name}",
                            kind=EdgeKind.RAISES,
                            attributes={"lineno": child.lineno},
                        )
                    )

            # CATCHES
            elif isinstance(child, ast.ExceptHandler):
                if child.type:
                    exc_name = ast.unparse(child.type)
                    self.edges.append(
                        GraphEdge(
                            source_id=func_id,
                            target_id=f"exception:{exc_name}",
                            kind=EdgeKind.CATCHES,
                            attributes={"lineno": child.lineno},
                        )
                    )

            # TEST ASSERTIONS / TESTS edge
            elif isinstance(child, ast.Assert):
                test_node = self.nodes.get(func_id)
                if test_node and test_node.kind == NodeKind.TEST_FUNCTION:
                    assert_str = ast.unparse(child.test)
                    # Extract any symbols mentioned in the assertion
                    for sub in ast.walk(child.test):
                        if isinstance(sub, ast.Name):
                            self.edges.append(
                                GraphEdge(
                                    source_id=func_id,
                                    target_id=f"symbol:{sub.id}",
                                    kind=EdgeKind.TESTS,
                                    attributes={"assertion": assert_str, "lineno": child.lineno},
                                )
                            )


def parse_source_file(file_path: str, source_code: Optional[str] = None) -> Tuple[List[GraphNode], List[GraphEdge]]:
    """Parses a Python file and returns extracted GraphNodes and GraphEdges."""
    if source_code is None:
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                source_code = f.read()
        except Exception as e:
            logger.warning(f"Failed to read file {file_path}: {e}")
            return [], []

    try:
        tree = ast.parse(source_code, filename=file_path)
    except SyntaxError as e:
        logger.debug(f"Syntax error parsing {file_path}: {e}")
        return [], []
    except Exception as e:
        logger.warning(f"Error parsing AST for {file_path}: {e}")
        return [], []

    extractor = SymbolExtractor(file_path, source_code)
    try:
        extractor.visit(tree)
    except Exception as e:
        logger.warning(f"Error extracting symbols from {file_path}: {e}")

    return list(extractor.nodes.values()), extractor.edges
