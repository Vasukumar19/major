"""Comprehensive unit tests for PatchForge Repository Intelligence & Graph Reasoning Engine."""
import json
import os
import pytest


from patchforge.repository_intelligence.agent_tools import RepositoryInvestigationTools
from patchforge.repository_intelligence.diagnosis import (
    BehavioralDiagnosisEngine,
    DiagnosisResult,
)
from patchforge.repository_intelligence.flow import (
    CFGBlock,
    ControlFlowBuilder,
    StateFlowAnalyzer,
    analyze_symbol_flow,
)
from patchforge.repository_intelligence.git_evidence import GitEvidenceExtractor
from patchforge.repository_intelligence.graph import RepositoryGraph
from patchforge.repository_intelligence.parser import parse_source_file
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
from patchforge.repository_intelligence.test_graph import TestGraphIndex


SAMPLE_MODULE_CODE = '''"""Sample module for testing."""
import os
from sys import argv

class BaseClient:
    def connect(self):
        return True

class HttpClient(BaseClient):
    """HTTP client subclass."""
    def __init__(self, timeout=30):
        self.timeout = timeout
        self.connected = False

    def send_request(self, method, url, data=None):
        self.connect()
        retries = 0
        while retries < 3:
            if not self.connected:
                self.connect()
            resp = self._execute(method, url)
            if resp:
                return resp
            retries += 1
        raise ConnectionError("Failed after retries")

    def _execute(self, method, url):
        return f"Response for {method} {url}"
'''

SAMPLE_TEST_CODE = '''"""Sample test file."""
from sample import HttpClient

class TestHttpClient:
    def test_send_request_success(self):
        client = HttpClient()
        res = client.send_request("GET", "http://example.com")
        assert res is not None
        assert "Response for GET" in res

    def test_send_request_failure(self):
        client = HttpClient()
        try:
            client.send_request("INVALID", "")
        except Exception as e:
            assert isinstance(e, ConnectionError)
'''


def test_schema_and_node_kind():
    node = GraphNode(
        id="sample.py:HttpClient.send_request",
        name="send_request",
        kind=NodeKind.METHOD,
        file_path="sample.py",
        start_line=15,
        end_line=25,
        signature="def send_request(self, method, url, data=None)",
    )
    edge = GraphEdge(
        source_id="sample.py:HttpClient.send_request",
        target_id="sample.py:BaseClient.connect",
        kind=EdgeKind.CALLS,
    )
    
    # Hashable & Comparable
    node_set = {node}
    assert node in node_set
    edge_set = {edge}
    assert edge in edge_set

    # Serialization
    d = node.to_dict()
    reconstructed = GraphNode.from_dict(d)
    assert reconstructed.id == node.id
    assert reconstructed.kind == NodeKind.METHOD


def test_symbol_extractor_and_parser():
    nodes, edges = parse_source_file("sample.py", SAMPLE_MODULE_CODE)
    node_names = {n.name for n in nodes}
    
    assert "sample.py" in {os.path.basename(n.file_path) for n in nodes}
    assert "BaseClient" in node_names
    assert "HttpClient" in node_names
    assert "send_request" in node_names
    assert "_execute" in node_names
    
    edge_kinds = {e.kind for e in edges}
    assert EdgeKind.DEFINES in edge_kinds
    assert EdgeKind.CALLS in edge_kinds
    assert EdgeKind.INHERITS in edge_kinds


def test_repository_graph_queries():
    graph = RepositoryGraph("test_repo")
    nodes, edges = parse_source_file("sample.py", SAMPLE_MODULE_CODE)
    for n in nodes:
        graph.add_node(n)
    for e in edges:
        graph.add_edge(e)

    # Inheritance
    inh = graph.inheritance("HttpClient")
    assert "BaseClient" in inh["bases"]

    # Callees
    callees = graph.callees("send_request")
    callee_names = {c.name for c in callees}
    assert "_execute" in callee_names or "connect" in callee_names

    # Summary
    summary = graph.get_symbol_summary("send_request")
    assert summary["found"] is True
    assert summary["kind"] == NodeKind.METHOD.value


def test_control_flow_and_state_flow():
    cfg_str, state_str = analyze_symbol_flow(SAMPLE_MODULE_CODE, "send_request")
    assert cfg_str is not None
    assert "while: retries < 3" in cfg_str
    assert "if: not self.connected" in cfg_str
    assert "raise: ConnectionError" in cfg_str

    assert state_str is not None
    assert "retries" in state_str
    assert "Loop-Carried State" in state_str


def test_test_graph_index_and_traceback_linking():
    test_index = TestGraphIndex()
    test_index.index_test_file("test_sample.py", SAMPLE_TEST_CODE)
    
    assert "test_send_request_success" in test_index.name_to_tests
    tests_for_send = test_index.tests_for_symbol("send_request")
    assert len(tests_for_send) >= 1

    traceback = '''
________________ TestHttpClient.test_send_request_failure ________________
File "sample.py", line 25, in send_request
    raise ConnectionError("Failed after retries")
ConnectionError: Failed after retries
'''
    parsed = test_index.link_execution_traceback(traceback)
    assert parsed["failing_test_name"] == "test_send_request_failure"
    assert parsed["failing_file"] == "sample.py"
    assert parsed["failing_line"] == 25
    assert "send_request" in parsed["implicated_symbols"]
    assert parsed["error_type"] == "ConnectionError"


def test_behavioral_diagnosis_engine():
    node = GraphNode(
        id="sample.py:send_request",
        name="send_request",
        kind=NodeKind.METHOD,
        file_path="sample.py",
        start_line=15,
        end_line=25,
    )
    ctx = RepairContext(
        primary_node=node,
        primary_source="def send_request(...):\n    pass",
        callers=[],
        callees=[],
    )
    prompt = BehavioralDiagnosisEngine.build_diagnostic_prompt("Sample issue", ctx)
    assert "### ISSUE SPECIFICATION:" in prompt
    assert "=== CAUSE ===" in prompt

    sample_response = '''
=== CAUSE ===
The loop counter was not properly initialized and overflows.

=== INVARIANT ===
All valid requests must return response strings.

=== REPAIR STRATEGY ===
Reset retries before the loop and handle 404 responses gracefully.

=== AFFECTED SITES ===
sample.py:send_request
'''
    diag = BehavioralDiagnosisEngine.parse_diagnosis(sample_response)
    assert "loop counter" in diag.cause
    assert "valid requests" in diag.invariant
    assert "Reset retries" in diag.repair_strategy
    assert "sample.py:send_request" in diag.affected_sites


def test_graph_aware_failure_refinement():
    graph = RepositoryGraph("test_repo")
    nodes, edges = parse_source_file("sample.py", SAMPLE_MODULE_CODE)
    for n in nodes:
        graph.add_node(n)
    for e in edges:
        graph.add_edge(e)

    test_index = TestGraphIndex()
    test_index.index_test_file("test_sample.py", SAMPLE_TEST_CODE)

    refiner = GraphAwareFailureRefiner(".", graph, test_index)
    traceback = '''
File "sample.py", line 28, in _execute
    raise ValueError("Invalid method")
ValueError: Invalid method
'''
    evidence = refiner.analyze_failure(
        primary_symbol="send_request",
        primary_file="sample.py",
        failure_traceback=traceback,
    )
    assert evidence.failing_symbol == "_execute"
    assert evidence.relationship_to_primary == "CALLED_BY_PRIMARY"
    assert "CALLED by primary target" in evidence.structural_explanation


def test_investigation_tools():
    graph = RepositoryGraph("test_repo")
    nodes, edges = parse_source_file("sample.py", SAMPLE_MODULE_CODE)
    for n in nodes:
        graph.add_node(n)
    for e in edges:
        graph.add_edge(e)

    test_index = TestGraphIndex()
    test_index.index_test_file("test_sample.py", SAMPLE_TEST_CODE)

    tools = RepositoryInvestigationTools(".", graph, test_index)
    sym_info = tools.inspect_symbol("HttpClient")
    assert sym_info["found"] is True
    assert sym_info["kind"] == NodeKind.CLASS.value

    callees = tools.inspect_callees("send_request")
    assert any(c["name"] in ("_execute", "connect") for c in callees)
