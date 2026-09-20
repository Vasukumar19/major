"""PatchForge Repository Intelligence & Graph Reasoning Subsystem."""

from patchforge.repository_intelligence.agent_tools import RepositoryInvestigationTools
from patchforge.repository_intelligence.diagnosis import (
    BehavioralDiagnosisEngine,
    DiagnosisResult,
)
from patchforge.repository_intelligence.flow import (
    CFGBlock,
    ControlFlowBuilder,
    StateFlowAnalyzer,
    StateFlowSummary,
    analyze_symbol_flow,
)
from patchforge.repository_intelligence.git_evidence import GitEvidenceExtractor
from patchforge.repository_intelligence.graph import RepositoryGraph
from patchforge.repository_intelligence.indexer import RepositoryIndexer
from patchforge.repository_intelligence.parser import SymbolExtractor, parse_source_file
from patchforge.repository_intelligence.refinement import (
    GraphAwareFailureRefiner,
    GraphRefinementEvidence,
)
from patchforge.repository_intelligence.retriever import (
    RepairContext,
    RepairContextRetriever,
)
from patchforge.repository_intelligence.schema import (
    EdgeKind,
    GraphEdge,
    GraphNode,
    NodeKind,
)
from patchforge.repository_intelligence.test_graph import (
    TestCaseEntity,
    TestGraphIndex,
)

__all__ = [
    "NodeKind",
    "EdgeKind",
    "GraphNode",
    "GraphEdge",
    "RepositoryGraph",
    "SymbolExtractor",
    "parse_source_file",
    "CFGBlock",
    "ControlFlowBuilder",
    "StateFlowAnalyzer",
    "StateFlowSummary",
    "analyze_symbol_flow",
    "TestCaseEntity",
    "TestGraphIndex",
    "GitEvidenceExtractor",
    "RepositoryIndexer",
    "RepairContext",
    "RepairContextRetriever",
    "BehavioralDiagnosisEngine",
    "DiagnosisResult",
    "GraphAwareFailureRefiner",
    "GraphRefinementEvidence",
    "RepositoryInvestigationTools",
]
