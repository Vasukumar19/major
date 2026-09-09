"""Unit tests for Phase 0 Control-Flow Guards in PatchForge v0.3."""
import json
from unittest.mock import MagicMock
import pytest

from patchforge.agent.controller import AgentController
from patchforge.agent.policy import ActionDecision, AgentPolicy
from patchforge.agent.state import AgentPhase, AgentState
from patchforge.issue.problem import Problem
from patchforge.models.provider import Generation
from patchforge.reasoning.understanding import ProgramUnderstanding
from patchforge.retrieval.evidence import Evidence, EvidenceType
from patchforge.tools.base import ToolCall, ToolResult
from patchforge.tools import create_default_tool_registry


def test_distinct_evidence_counting():
    prob = Problem(instance_id="p-1", problem_statement="Fix bug", repo="test", base_commit="abc")
    state = AgentState(instance_id="p-1", problem=prob)

    # 1. Add identical evidence items (same file, same symbol, same type)
    ev1 = Evidence(source="s1", type=EvidenceType.IDENTIFIER_MATCH.value, file="a.py", symbol="foo")
    ev2 = Evidence(source="s2", type=EvidenceType.IDENTIFIER_MATCH.value, file="a.py", symbol="foo")
    state.add_evidence(ev1)
    state.add_evidence(ev2)
    assert state.distinct_evidence_count() == 1

    # 2. Add different symbol
    ev3 = Evidence(source="s3", type=EvidenceType.IDENTIFIER_MATCH.value, file="a.py", symbol="bar")
    state.add_evidence(ev3)
    assert state.distinct_evidence_count() == 2

    # 3. Add different file
    ev4 = Evidence(source="s4", type=EvidenceType.STATIC_REFERENCE.value, file="b.py")
    state.add_evidence(ev4)
    assert state.distinct_evidence_count() == 3


def test_exact_signature_repetition_guard_blocks():
    policy = AgentPolicy()
    prob = Problem(instance_id="p-1", problem_statement="Fix bug", repo="test", base_commit="abc")
    state = AgentState(instance_id="p-1", problem=prob)

    # Simulate recent call
    state.recent_tool_calls.append({
        "name": "find_symbol",
        "arguments": {"symbol_name": "__init__", "file_path": "requests/models.py"},
    })

    # Propose exact duplicate call
    call = ToolCall(name="find_symbol", arguments={"symbol_name": "__init__", "file_path": "requests/models.py"})
    decision = policy.evaluate_action(state, call)

    assert decision.allowed is False
    assert "repeated an identical tool call" in decision.blocked_reason
    assert state.guard_firings["exact_repetition_blocks"] == 1


def test_semantic_zero_info_gain_guard_blocks():
    policy = AgentPolicy(max_zero_info_turns=2)
    prob = Problem(instance_id="p-1", problem_statement="Fix bug", repo="test", base_commit="abc")
    state = AgentState(instance_id="p-1", problem=prob, phase=AgentPhase.INVESTIGATE)

    # Set streak to threshold
    state.zero_info_turn_streak = 2

    # Propose another broad search
    call = ToolCall(name="search_code", arguments={"query": "class Request"})
    decision = policy.evaluate_action(state, call)

    assert decision.allowed is False
    assert "No new code or structural information gained" in decision.blocked_reason
    assert state.guard_firings["zero_info_blocks"] == 1


def test_confidence_threshold_triggers_clean_exit():
    policy = AgentPolicy(target_confidence_threshold=0.70, min_distinct_evidence=2)
    prob = Problem(instance_id="p-1", problem_statement="Fix bug", repo="test", base_commit="abc")
    state = AgentState(instance_id="p-1", problem=prob, phase=AgentPhase.INVESTIGATE)

    # High confidence understanding
    state.understanding = ProgramUnderstanding(
        summary="Bug in hooks",
        expected_behavior="accept list",
        observed_behavior="error",
        behavioral_delta="delta",
        violated_invariants=[],
        key_files=["requests/models.py"],
        key_symbols=["Request.__init__"],
        confidence=0.85,
    )
    # Add 2 distinct evidence items
    state.add_evidence(Evidence(source="s", type=EvidenceType.STATIC_REFERENCE.value, file="requests/models.py"))
    state.add_evidence(Evidence(source="s", type=EvidenceType.IDENTIFIER_MATCH.value, file="requests/models.py", symbol="Request.__init__"))

    call = ToolCall(name="search_code", arguments={"query": "def __init__"})
    decision = policy.evaluate_action(state, call)

    assert decision.allowed is False
    assert decision.next_phase == AgentPhase.HYPOTHESIZE
    assert decision.transition_trigger == "CONFIDENCE_THRESHOLD"
    assert state.localization_transition_trigger == "CONFIDENCE_THRESHOLD"
    assert state.guard_firings["confidence_transitions"] == 1
    assert "Target confidence threshold reached" in decision.blocked_reason


def test_circuit_breaker_triggers_unconditional_exit_at_max_turns():
    policy = AgentPolicy(max_localization_turns=4)
    prob = Problem(instance_id="p-1", problem_statement="Fix bug", repo="test", base_commit="abc")
    state = AgentState(instance_id="p-1", problem=prob, phase=AgentPhase.INVESTIGATE)

    # Localization turns reached limit with low confidence
    state.localization_turn_count = 4
    state.understanding = ProgramUnderstanding(
        summary="Unclear bug",
        expected_behavior="",
        observed_behavior="",
        behavioral_delta="",
        violated_invariants=[],
        confidence=0.3,
    )

    call = ToolCall(name="find_symbol", arguments={"symbol_name": "unknown"})
    decision = policy.evaluate_action(state, call)

    assert decision.allowed is False
    assert decision.next_phase == AgentPhase.HYPOTHESIZE
    assert decision.transition_trigger == "CIRCUIT_BREAKER_TURN_LIMIT"
    assert state.localization_transition_trigger == "CIRCUIT_BREAKER_TURN_LIMIT"
    assert state.guard_firings["circuit_breaker_transitions"] == 1
    assert "circuit breaker" in decision.blocked_reason


def test_controller_handles_blocked_action_and_recovers(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "core.py").write_text("def run():\n    return 42\n", encoding="utf-8")

    prob = Problem(instance_id="p-1", problem_statement="Fix run return", repo="test", base_commit="abc")
    tools = create_default_tool_registry(repo_dir=str(tmp_path))

    mock_provider = MagicMock()
    # Turn 1: LLM calls find_symbol
    resp1 = Generation(
        text=json.dumps({
            "thought": "Find run",
            "action": {"name": "find_symbol", "arguments": {"symbol_name": "run"}},
        }),
        input_tokens=10, output_tokens=10, cost_usd=0.001, model="dummy",
    )
    # Turn 2: LLM attempts identical call (should be BLOCKED by repetition guard)
    resp2 = Generation(
        text=json.dumps({
            "thought": "Find run again",
            "action": {"name": "find_symbol", "arguments": {"symbol_name": "run"}},
        }),
        input_tokens=10, output_tokens=10, cost_usd=0.001, model="dummy",
    )
    # Turn 3: LLM reacts to block and concludes
    resp3 = Generation(
        text=json.dumps({
            "thought": "I will finish now",
            "action": {"name": "done", "arguments": {}},
        }),
        input_tokens=10, output_tokens=10, cost_usd=0.001, model="dummy",
    )
    mock_provider.generate_one.side_effect = [
        Generation(text="{}", input_tokens=5, output_tokens=5, cost_usd=0.0, model="dummy"),  # derive_understanding
        resp1,
        resp2,
        resp3,
    ]

    policy = AgentPolicy(max_turns=5)
    controller = AgentController(
        provider=mock_provider,
        tools=tools,
        policy=policy,
        model="dummy",
    )

    patch, traj, state = controller.run(prob, repo_dir=str(tmp_path))
    assert state.guard_firings["exact_repetition_blocks"] == 1
    assert any(s.tool_result and s.tool_result.get("status") == "BLOCKED" for s in traj.steps)
    assert state.phase == AgentPhase.DONE
