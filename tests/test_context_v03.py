"""Unit tests for Context Compactor and Program Understanding in PatchForge v0.3."""
import pytest
from unittest.mock import MagicMock

from patchforge.agent.context import ContextCompactor
from patchforge.agent.state import AgentPhase, AgentState, ExecutionEvidence
from patchforge.agent.trajectory import AgentTrajectory
from patchforge.issue.problem import Problem
from patchforge.models.provider import Generation
from patchforge.reasoning.hypothesis import Hypothesis
from patchforge.reasoning.understanding import ProgramUnderstanding, derive_understanding
from patchforge.retrieval.evidence import Evidence, EvidenceType
from patchforge.tools.base import ToolCall, ToolResult


def test_program_understanding_serialization():
    pu = ProgramUnderstanding(
        summary="Missing query param encoding",
        expected_behavior="URL should encode space as %20",
        observed_behavior="URL left unencoded",
        behavioral_delta="Encoding omitted in build_url",
        violated_invariants=["RFC 3986 URL encoding invariant"],
        key_symbols=["build_url"],
        key_files=["requests/models.py"],
        confidence=0.9,
    )
    d = pu.to_dict()
    assert d["summary"] == "Missing query param encoding"
    assert "RFC 3986" in pu.format_summary()


def test_derive_understanding_fallback():
    mock_provider = MagicMock()
    mock_provider.generate_one.return_value = Generation(
        text="Invalid json response",
        input_tokens=10,
        output_tokens=10,
        cost_usd=0.001,
        model="dummy",
    )
    prob = Problem(
        instance_id="test-1",
        problem_statement="Test statement error in get()",
        repo="test/repo",
        base_commit="abc",
    )
    pu = derive_understanding(prob, mock_provider)
    assert pu.summary.startswith("Test statement")


def test_context_compactor_assembly():
    prob = Problem(
        instance_id="test-1",
        problem_statement="Short issue description",
        repo="test/repo",
        base_commit="abc",
    )
    state = AgentState(instance_id="test-1", problem=prob)
    state.understanding = ProgramUnderstanding(summary="Summary 1", expected_behavior="Exp", observed_behavior="Obs")
    state.pinned_snippets["requests/models.py"] = "def send():\n    pass"
    state.evidence_store.append(Evidence(source="test", type=EvidenceType.STATIC_REFERENCE.value, file="requests/models.py", symbol="send"))
    state.last_execution_evidence = ExecutionEvidence(failing_tests=["test_send"], traceback="Traceback details")

    traj = AgentTrajectory(instance_id="test-1")
    compactor = ContextCompactor()
    prompt = compactor.compact_prompt(state, traj)

    assert "=== PROBLEM STATEMENT ===" in prompt
    assert "=== CURRENT PROGRAM UNDERSTANDING ===" in prompt
    assert "=== RELEVANT CODE SNIPPETS ===" in prompt
    assert "=== LATEST EXECUTION FAILURE ===" in prompt


def test_context_compactor_preserves_latest_raw_read_content():
    prob = Problem("test-raw", "Fix the function", "test/repo", "abc")
    state = AgentState(instance_id="test-raw", problem=prob)
    traj = AgentTrajectory(instance_id="test-raw")
    raw = "17: def request(method, url, **kwargs):\n18:     return response\n"
    traj.record_step(
        turn=1,
        phase="HYPOTHESIZE",
        thought="Read target",
        tool_call=ToolCall("read_file", {"file_path": "requests/api.py"}),
        tool_result=ToolResult(
            "read_file",
            "SUCCESS",
            data={"file_path": "requests/api.py", "content": raw},
            message="Read target",
        ),
    )

    prompt = ContextCompactor().compact_prompt(state, traj)

    assert "=== MOST RECENT FILE CONTENT (VERBATIM) ===" in prompt
    assert raw in prompt


def test_later_read_updates_pinned_snippet_and_prompt():
    prob = Problem("test-refresh", "Fix the function", "test/repo", "abc")
    state = AgentState(instance_id="test-refresh", problem=prob)
    state.record_file_visit("pkg/core.py", "1: old content")
    state.record_file_visit("pkg/core.py", "20: refreshed content")

    prompt = ContextCompactor().compact_prompt(state, AgentTrajectory("test-refresh"))

    assert "20: refreshed content" in prompt
    assert "1: old content" not in prompt
