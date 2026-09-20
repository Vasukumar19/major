"""Test Graph Index linking tests, assertions, symbols, and failure tracebacks."""
import ast
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


@dataclass
class TestAssertion:
    line: int
    assertion_code: str
    symbols_tested: List[str]


@dataclass
class TestCaseEntity:
    name: str
    file_path: str
    class_name: Optional[str]
    start_line: int
    end_line: int
    docstring: Optional[str] = None
    assertions: List[TestAssertion] = field(default_factory=list)
    symbols_referenced: List[str] = field(default_factory=list)
    fixtures: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "file_path": self.file_path,
            "class_name": self.class_name,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "docstring": self.docstring,
            "assertions": [
                {"line": a.line, "code": a.assertion_code, "symbols": a.symbols_tested}
                for a in self.assertions
            ],
            "symbols_referenced": self.symbols_referenced,
            "fixtures": self.fixtures,
        }


class TestExtractor(ast.NodeVisitor):
    """Parses a test file to extract structured test cases, assertions, and referenced symbols."""

    def __init__(self, file_path: str, source_code: str):
        self.file_path = file_path.replace("\\", "/")
        self.source_code = source_code
        self.tests: List[TestCaseEntity] = []
        self.current_class: Optional[str] = None

    def visit_ClassDef(self, node: ast.ClassDef):
        old_class = self.current_class
        if node.name.startswith("Test") or "Test" in node.name:
            self.current_class = node.name
        self.generic_visit(node)
        self.current_class = old_class

    def visit_FunctionDef(self, node: ast.FunctionDef):
        if node.name.startswith("test_") or node.name.endswith("_test"):
            end_line = getattr(node, "end_lineno", node.lineno)
            docstring = ast.get_docstring(node)
            
            assertions: List[TestAssertion] = []
            symbols_ref: Set[str] = set()
            fixtures: List[str] = [a.arg for a in node.args.args if a.arg != "self"]
            
            # Walk function body
            for child in ast.walk(node):
                if isinstance(child, ast.Assert):
                    code_str = ast.unparse(child.test)
                    syms = [s.id for s in ast.walk(child.test) if isinstance(s, ast.Name)]
                    assertions.append(TestAssertion(line=child.lineno, assertion_code=code_str, symbols_tested=syms))
                elif isinstance(child, ast.Call):
                    if isinstance(child.func, ast.Name):
                        symbols_ref.add(child.func.id)
                    elif isinstance(child.func, ast.Attribute):
                        symbols_ref.add(child.func.attr)
                elif isinstance(child, ast.Name):
                    symbols_ref.add(child.id)

            test_case = TestCaseEntity(
                name=node.name,
                file_path=self.file_path,
                class_name=self.current_class,
                start_line=node.lineno,
                end_line=end_line,
                docstring=docstring,
                assertions=assertions,
                symbols_referenced=list(symbols_ref),
                fixtures=fixtures,
            )
            self.tests.append(test_case)

        self.generic_visit(node)


class TestGraphIndex:
    """Repository-wide index of test cases, assertions, and execution failure trace mapping."""
    __test__ = False

    def __init__(self):
        self.tests: Dict[str, TestCaseEntity] = {}  # key: "file:test_name" or "file:Class.test_name"
        self.symbol_to_tests: Dict[str, List[TestCaseEntity]] = {}
        self.name_to_tests: Dict[str, List[TestCaseEntity]] = {}

    def index_test_file(self, file_path: str, source_code: str):
        try:
            tree = ast.parse(source_code, filename=file_path)
        except Exception as e:
            logger.debug(f"Failed to parse test file {file_path}: {e}")
            return

        extractor = TestExtractor(file_path, source_code)
        extractor.visit(tree)
        
        for t in extractor.tests:
            key = f"{t.file_path}:{t.class_name + '.' if t.class_name else ''}{t.name}"
            self.tests[key] = t
            
            if t.name not in self.name_to_tests:
                self.name_to_tests[t.name] = []
            self.name_to_tests[t.name].append(t)
            
            for sym in t.symbols_referenced:
                if sym not in self.symbol_to_tests:
                    self.symbol_to_tests[sym] = []
                self.symbol_to_tests[sym].append(t)

    def tests_for_symbol(self, symbol: str) -> List[TestCaseEntity]:
        """Returns all test cases that reference or assert on the symbol."""
        return self.symbol_to_tests.get(symbol, [])

    def link_execution_traceback(self, traceback_text: str) -> Dict[str, Any]:
        """Parses a failure traceback and correlates it with test entities and source files."""
        result = {
            "failing_test_name": None,
            "failing_file": None,
            "failing_line": None,
            "error_type": None,
            "error_message": None,
            "implicated_symbols": [],
            "matched_test_case": None,
        }
        
        # Match pytest failure headers like:
        # ____ test_POSTBIN_GET_POST_FILES_WITH_HEADERS ____
        # or ____ TestHttpClient.test_send_request_failure ____
        header_match = re.search(r"_{4,}\s+([A-Za-z0-9_\.]+)\s+_{4,}", traceback_text)
        if header_match:
            raw_test = header_match.group(1)
            test_name = raw_test.split(".")[-1]
            result["failing_test_name"] = test_name
            if test_name in self.name_to_tests:
                result["matched_test_case"] = self.name_to_tests[test_name][0].to_dict()


        # Extract file and line matches (e.g. File "requests/models.py", line 463, in register_hook)
        file_matches = re.findall(r'File "([^"]+)", line (\d+)(?:, in ([A-Za-z0-9_]+))?', traceback_text)
        if file_matches:
            # The last match in repo code is typically the innermost failing site
            repo_matches = [m for m in file_matches if not any(x in m[0] for x in ("site-packages", "<string>", "pytest"))]
            if repo_matches:
                innermost = repo_matches[-1]
                result["failing_file"] = innermost[0]
                result["failing_line"] = int(innermost[1])
                if innermost[2]:
                    result["implicated_symbols"].append(innermost[2])

        # Extract error message
        err_match = re.search(r"([A-Za-z0-9_]+Error|AssertionError):\s*(.*)", traceback_text)
        if err_match:
            result["error_type"] = err_match.group(1)
            result["error_message"] = err_match.group(2).strip()

        return result
