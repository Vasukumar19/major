"""Graph-Aware Refinement: converts test failure tracebacks into structural graph queries and updated repair targets."""
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from patchforge.repository_intelligence.graph import RepositoryGraph
from patchforge.repository_intelligence.retriever import RepairContext, RepairContextRetriever
from patchforge.repository_intelligence.schema import EdgeKind, GraphNode
from patchforge.repository_intelligence.test_graph import TestGraphIndex

logger = logging.getLogger(__name__)


@dataclass
class GraphRefinementEvidence:
    failing_symbol: str
    failing_file: str
    failing_line: int
    relationship_to_primary: str  # e.g., "CALLED_BY_PRIMARY", "CALLER_OF_PRIMARY", "SAME_SITE"
    error_message: str
    suggested_additional_site: Optional[str]
    structural_explanation: str


class GraphAwareFailureRefiner:
    """Analyzes test execution failures using graph intelligence to refine the diagnosis and repair targets."""

    def __init__(self, repo_dir: str, graph: RepositoryGraph, test_index: TestGraphIndex):
        self.repo_dir = repo_dir
        self.graph = graph
        self.test_index = test_index

    def analyze_failure(
        self,
        primary_symbol: str,
        primary_file: str,
        failure_traceback: str,
    ) -> GraphRefinementEvidence:
        """Traces execution failure through the repository graph to uncover latent secondary defects."""
        parsed = self.test_index.link_execution_traceback(failure_traceback)
        
        implicated_syms = parsed.get("implicated_symbols", [])
        failing_sym = implicated_syms[0] if implicated_syms else primary_symbol
        failing_file = parsed.get("failing_file") or primary_file
        failing_line = parsed.get("failing_line") or 0
        err_msg = f"{parsed.get('error_type', 'Error')}: {parsed.get('error_message', 'Test failed')}"

        # Determine structural relationship between primary symbol and failing site
        relationship = "SAME_SITE"
        suggested_site = None
        explanation = f"Execution failed directly within primary target {primary_symbol}."

        if failing_sym != primary_symbol:
            # Check if primary calls failing_sym (helper failure)
            primary_callees = {c.name for c in self.graph.callees(primary_symbol)}
            primary_callers = {c.name for c in self.graph.callers(primary_symbol)}

            if failing_sym in primary_callees:
                relationship = "CALLED_BY_PRIMARY"
                suggested_site = f"{failing_file}:{failing_sym}"
                explanation = (
                    f"Test failed inside helper function '{failing_sym}' which is CALLED by primary target '{primary_symbol}'. "
                    f"The patch in '{primary_symbol}' may have passed invalid arguments or failed to handle state returned by '{failing_sym}'."
                )
            elif failing_sym in primary_callers:
                relationship = "CALLER_OF_PRIMARY"
                suggested_site = f"{failing_file}:{failing_sym}"
                explanation = (
                    f"Test failed in caller '{failing_sym}' which INVOKES primary target '{primary_symbol}'. "
                    f"The patch may have broken the interface contract expected by '{failing_sym}'."
                )
            else:
                relationship = "CROSS_MODULE"
                suggested_site = f"{failing_file}:{failing_sym}" if failing_file != primary_file else None
                explanation = f"Failure occurred at '{failing_sym}' at line {failing_line}."

        return GraphRefinementEvidence(
            failing_symbol=failing_sym,
            failing_file=failing_file,
            failing_line=failing_line,
            relationship_to_primary=relationship,
            error_message=err_msg,
            suggested_additional_site=suggested_site,
            structural_explanation=explanation,
        )

    def build_refinement_prompt(
        self,
        problem_statement: str,
        repair_context: RepairContext,
        evidence: GraphRefinementEvidence,
        previous_patch: str,
    ) -> str:
        """Constructs a graph-informed refinement prompt."""
        return f"""### REFINEMENT CYCLE — GRAPH CAUSAL DIAGNOSIS

The previous patch failed test execution. The Repository Intelligence Graph analyzed the execution traceback:

### GRAPH FAILURE ANALYSIS:
- Failing Location: {evidence.failing_file}:L{evidence.failing_line} in `{evidence.failing_symbol}`
- Relationship to Primary Target (`{repair_context.primary_node.name}`): {evidence.relationship_to_primary}
- Error: {evidence.error_message}
- Structural Insight: {evidence.structural_explanation}
{"- Suggested Additional Repair Site: " + evidence.suggested_additional_site if evidence.suggested_additional_site else ""}

### PREVIOUS FAILED PATCH:
```diff
{previous_patch}
```

### INSTRUCTIONS:
Revise your diagnosis and repair strategy:
1. Address why the previous patch caused `{evidence.failing_symbol}` to fail.
2. If a secondary site is implicated, update both sites consistently.
3. Ensure no regression invariants are violated.
"""
