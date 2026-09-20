"""Graph-aware context retrieval and token-budgeted RepairContext builder."""
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from patchforge.repository_intelligence.flow import analyze_symbol_flow
from patchforge.repository_intelligence.git_evidence import GitEvidenceExtractor
from patchforge.repository_intelligence.graph import RepositoryGraph
from patchforge.repository_intelligence.schema import GraphNode, NodeKind
from patchforge.repository_intelligence.test_graph import TestCaseEntity, TestGraphIndex

logger = logging.getLogger(__name__)


@dataclass
class RepairContext:
    primary_node: GraphNode
    primary_source: str
    callers: List[GraphNode] = field(default_factory=list)
    callees: List[GraphNode] = field(default_factory=list)
    related_symbols: List[GraphNode] = field(default_factory=list)
    relevant_tests: List[TestCaseEntity] = field(default_factory=list)
    control_flow: Optional[str] = None
    state_flow: Optional[str] = None
    git_history: Optional[str] = None

    def to_diagnostic_prompt(self, max_chars: int = 16000) -> str:
        """Formats context into a structured diagnostic workspace strictly respecting character/token budget."""
        sections = []
        
        # 1. Primary Target Code
        sections.append(f"### PRIMARY TARGET: {self.primary_node.name} ({self.primary_node.file_path}:L{self.primary_node.start_line}-L{self.primary_node.end_line})\n```python\n{self.primary_source}\n```")
        
        # 2. Control Flow & State Flow
        if self.control_flow or self.state_flow:
            flow_sec = ["### STRUCTURAL & CAUSAL FLOW:"]
            if self.control_flow:
                flow_sec.append(f"#### Control-Flow Hierarchy (CFG):\n{self.control_flow}")
            if self.state_flow:
                flow_sec.append(f"#### Variable State Flow:\n{self.state_flow}")
            sections.append("\n".join(flow_sec))

        # 3. Call Graph Context (Callers & Callees)
        call_sec = ["### CALL GRAPH RELATIONSHIPS:"]
        if self.callers:
            call_sec.append("Callers (Incoming Dependencies):")
            for c in self.callers[:5]:
                sig = c.signature or c.name
                call_sec.append(f"  * {sig} ({c.file_path}:L{c.start_line})")
        else:
            call_sec.append("Callers: None detected")

        if self.callees:
            call_sec.append("Callees (Functions/Methods Invoked):")
            for c in self.callees[:6]:
                sig = c.signature or c.name
                call_sec.append(f"  * {sig} ({c.file_path}:L{c.start_line})")
        else:
            call_sec.append("Callees: None detected")
        sections.append("\n".join(call_sec))

        # 4. Relevant Tests & Assertions
        if self.relevant_tests:
            test_sec = ["### RELEVANT TEST SPECIFICATIONS:"]
            for t in self.relevant_tests[:4]:
                test_sec.append(f"- Test `{t.name}` in `{t.file_path}`:")
                if t.docstring:
                    test_sec.append(f"    Intent: {t.docstring.strip()[:100]}")
                if t.assertions:
                    test_sec.append("    Key Assertions:")
                    for a in t.assertions[:3]:
                        test_sec.append(f"      L{a.line}: assert {a.assertion_code[:80]}")
            sections.append("\n".join(test_sec))

        # 5. Git / Temporal Evidence
        if self.git_history:
            sections.append(f"### TEMPORAL / GIT CONTEXT:\n{self.git_history}")

        # Assemble and trim to budget
        full_text = "\n\n".join(sections)
        if len(full_text) > max_chars:
            full_text = full_text[:max_chars - 100] + "\n... [Context truncated to fit token budget]"
        return full_text


class RepairContextRetriever:
    """Retrieves ranked, graph-enriched, token-budgeted context for a given issue and candidate target."""

    def __init__(self, repo_dir: str, graph: RepositoryGraph, test_index: TestGraphIndex):
        self.repo_dir = repo_dir
        self.graph = graph
        self.test_index = test_index
        self.git_extractor = GitEvidenceExtractor(repo_dir)

    def retrieve(
        self,
        problem_statement: str,
        target_file: str,
        target_symbol: str,
        max_budget_chars: int = 16000,
    ) -> Optional[RepairContext]:
        """Retrieves and packages graph context for the primary target."""
        # 1. Resolve Primary Node
        nodes = self.graph.resolve_symbol(target_symbol)
        primary_node = None
        
        # Prefer node matching file path
        norm_file = target_file.replace("\\", "/")
        for n in nodes:
            if n.file_path.replace("\\", "/") == norm_file:
                primary_node = n
                break
        if not primary_node and nodes:
            primary_node = nodes[0]

        if not primary_node:
            logger.warning(f"Could not resolve primary node for {target_symbol} in {target_file}")
            return None

        # 2. Extract Source Code
        full_path = os.path.join(self.repo_dir, primary_node.file_path)
        primary_source = ""
        full_file_code = ""
        if os.path.exists(full_path):
            try:
                with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                    full_file_code = f.read()
                lines = full_file_code.splitlines()
                start = max(0, primary_node.start_line - 1)
                end = min(len(lines), primary_node.end_line)
                primary_source = "\n".join(lines[start:end])
            except Exception as e:
                logger.warning(f"Failed to read source {full_path}: {e}")

        # 3. Callers, Callees, and Related Symbols
        callers = self.graph.callers(primary_node.name)
        callees = self.graph.callees(primary_node.name)
        related = self.graph.related_symbols(primary_node.name)

        # 4. Relevant Tests
        tests = self.test_index.tests_for_symbol(primary_node.name)
        if not tests:
            # Check for issue keywords in test names
            issue_words = set(re.findall(r"[A-Za-z0-9_]+", problem_statement.lower()))
            for name, test_list in self.test_index.name_to_tests.items():
                if any(w in name.lower() for w in issue_words if len(w) > 4):
                    tests.extend(test_list[:2])
                    if len(tests) >= 5:
                        break

        # 5. Control & State Flow
        cfg_str, state_str = None, None
        if full_file_code:
            cfg_str, state_str = analyze_symbol_flow(full_file_code, primary_node.name)

        # 6. Git History
        git_hist = self.git_extractor.symbol_history_summary(
            full_path, primary_node.name, primary_node.start_line, primary_node.end_line
        )

        return RepairContext(
            primary_node=primary_node,
            primary_source=primary_source,
            callers=callers,
            callees=callees,
            related_symbols=related,
            relevant_tests=tests,
            control_flow=cfg_str,
            state_flow=state_str,
            git_history=git_hist,
        )
