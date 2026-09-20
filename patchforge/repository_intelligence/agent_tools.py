"""Structured Agent Investigation Tools querying the Repository Intelligence Engine."""
import logging
import os
from typing import Any, Dict, List, Optional

from patchforge.repository_intelligence.flow import analyze_symbol_flow
from patchforge.repository_intelligence.git_evidence import GitEvidenceExtractor
from patchforge.repository_intelligence.graph import RepositoryGraph
from patchforge.repository_intelligence.test_graph import TestGraphIndex

logger = logging.getLogger(__name__)


class RepositoryInvestigationTools:
    """High-value structured tools allowing autonomous agents to query repository intelligence."""

    def __init__(self, repo_dir: str, graph: RepositoryGraph, test_index: TestGraphIndex):
        self.repo_dir = os.path.abspath(repo_dir)
        self.graph = graph
        self.test_index = test_index
        self.git_extractor = GitEvidenceExtractor(self.repo_dir)

    def inspect_symbol(self, symbol_name: str) -> Dict[str, Any]:
        """Inspects definition, signature, line numbers, docstring, and immediate relations for a symbol."""
        return self.graph.get_symbol_summary(symbol_name)

    def inspect_callers(self, symbol_name: str) -> List[Dict[str, Any]]:
        """Finds all functions/methods that call the specified symbol."""
        nodes = self.graph.callers(symbol_name)
        return [
            {"name": n.name, "file_path": n.file_path, "line": n.start_line, "signature": n.signature}
            for n in nodes[:15]
        ]

    def inspect_callees(self, symbol_name: str) -> List[Dict[str, Any]]:
        """Finds all functions/methods called by the specified symbol."""
        nodes = self.graph.callees(symbol_name)
        return [
            {"name": n.name, "file_path": n.file_path, "line": n.start_line, "signature": n.signature}
            for n in nodes[:15]
        ]

    def inspect_references(self, symbol_name: str) -> List[Dict[str, Any]]:
        """Finds references to and from this symbol across the repository."""
        nodes = self.graph.references(symbol_name)
        return [
            {"name": n.name, "file_path": n.file_path, "kind": n.kind.value, "line": n.start_line}
            for n in nodes[:15]
        ]

    def inspect_tests(self, symbol_name: str) -> List[Dict[str, Any]]:
        """Finds all test cases asserting on or referencing the specified symbol."""
        tests = self.test_index.tests_for_symbol(symbol_name)
        return [t.to_dict() for t in tests[:10]]

    def inspect_control_flow(self, file_path: str, symbol_name: str) -> str:
        """Extracts the bounded control-flow tree (CFG) for a function/method."""
        full_p = os.path.join(self.repo_dir, file_path) if not os.path.isabs(file_path) else file_path
        if not os.path.exists(full_p):
            return f"Error: File {file_path} not found."
        with open(full_p, "r", encoding="utf-8", errors="replace") as f:
            code = f.read()
        cfg_str, _ = analyze_symbol_flow(code, symbol_name)
        return cfg_str or f"No function '{symbol_name}' found in {file_path} for CFG."

    def inspect_state_flow(self, file_path: str, symbol_name: str) -> str:
        """Extracts variable transitions, assignments, mutations, and loop-carried state."""
        full_p = os.path.join(self.repo_dir, file_path) if not os.path.isabs(file_path) else file_path
        if not os.path.exists(full_p):
            return f"Error: File {file_path} not found."
        with open(full_p, "r", encoding="utf-8", errors="replace") as f:
            code = f.read()
        _, state_str = analyze_symbol_flow(code, symbol_name)
        return state_str or f"No function '{symbol_name}' found in {file_path} for state flow."

    def inspect_git_history(self, file_path: str, symbol_name: str) -> str:
        """Extracts recent commit history and blame for the symbol."""
        full_p = os.path.join(self.repo_dir, file_path) if not os.path.isabs(file_path) else file_path
        nodes = self.graph.resolve_symbol(symbol_name)
        start_line = nodes[0].start_line if nodes else 1
        end_line = nodes[0].end_line if nodes else 100
        return self.git_extractor.symbol_history_summary(full_p, symbol_name, start_line, end_line)

    def inspect_failure(self, traceback_text: str) -> Dict[str, Any]:
        """Parses and correlates a failure traceback against test and symbol index."""
        return self.test_index.link_execution_traceback(traceback_text)
