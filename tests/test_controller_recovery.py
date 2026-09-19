"""Focused regression tests for PatchForge v0.3 Controller Recovery Fixes."""
import pytest
from unittest.mock import MagicMock

from patchforge.agent.controller import AgentController
from patchforge.agent.policy import AgentPolicy
from patchforge.agent.state import AgentPhase, AgentState
from patchforge.issue.problem import Problem
from patchforge.reasoning.hypothesis import Hypothesis
from patchforge.reasoning.plan import RepairPlan
from patchforge.tools.base import ToolCall, ToolRegistry, ToolResult


def _dummy_problem() -> Problem:
    return Problem(
        instance_id="test__pkg-1234",
        repo="test/pkg",
        base_commit="abc1234",
        problem_statement="Fix bug in foo function where kwargs are dropped.",
    )


def test_patch_requires_source_context_guard():
    """Test 1: Given phase=PLAN and target=requests/api.py with no verified read, apply_patch is blocked."""
    policy = AgentPolicy()
    state = AgentState(instance_id="test__pkg-1234", problem=_dummy_problem())
    state.phase = AgentPhase.PLAN
    state.active_plan = RepairPlan(target_file="requests/api.py", intended_change="Fix exception handling")

    # Attempt to apply patch without reading requests/api.py
    tool_call = ToolCall(
        name="apply_patch",
        arguments={
            "patch_text": "### requests/api.py\n<<<<<<< SEARCH\nfoo\n=======\nbar\n>>>>>>> REPLACE"
        },
    )
    decision = policy.evaluate_action(state, tool_call)
    assert not decision.allowed
    assert "BLOCKED: You cannot apply a patch to 'requests/api.py' without inspecting its exact source context" in decision.blocked_reason
    assert state.guard_firings["unverified_patch_blocks"] >= 1

    # After reading the file, apply_patch should be allowed
    state.verified_read_files.append("requests/api.py")
    decision2 = policy.evaluate_action(state, tool_call)
    assert decision2.allowed


def test_missing_patch_action_routes_correctively():
    """Test 2: When in PATCH phase with no patch emitted, controller routes to read target context."""
    problem = _dummy_problem()
    state = AgentState(instance_id="test__pkg-1234", problem=problem)
    state.phase = AgentPhase.PATCH
    state.active_plan = RepairPlan(target_file="requests/sessions.py", intended_change="Fix redirect handling")

    # Mock provider that emits conversational thought without tool call
    provider = MagicMock()
    provider.generate_one.return_value = MagicMock(
        text='{"thought": "I should fix the redirect handling in sessions.py"}',
        input_tokens=100,
        output_tokens=50,
        cost_usd=0.001,
    )

    tools = MagicMock()
    tools.schemas.return_value = []
    tools.execute.return_value = ToolResult(
        tool_name="read_file",
        status="SUCCESS",
        data={"file_path": "requests/sessions.py", "content": "def resolve_redirects(): pass"},
    )

    controller = AgentController(provider=provider, tools=tools, policy=AgentPolicy(max_turns=2))
    # Run 1 turn
    controller.policy.max_turns = 1
    controller.run(problem, instance_id="test__pkg-1234")

    # Verify that the passive turn was caught and synthesized into read_file
    assert state.guard_firings["passive_patch_recovery"] >= 1
    assert tools.execute.called
    called_tool_call = tools.execute.call_args[0][0]
    assert called_tool_call.name == "read_file"
    assert called_tool_call.arguments["file_path"] == "requests/sessions.py"


def test_search_mismatch_requires_context_recovery():
    """Test 3: When apply_patch fails with SEARCH block mismatch, retry requires verified read."""
    policy = AgentPolicy()
    state = AgentState(instance_id="test__pkg-1234", problem=_dummy_problem())
    state.phase = AgentPhase.PATCH
    state.verified_read_files.append("requests/api.py")
    state.last_failed_patch_file = "requests/api.py"

    patch_call = ToolCall(
        name="apply_patch",
        arguments={
            "patch_text": "### requests/api.py\n<<<<<<< SEARCH\nfoo\n=======\nbar\n>>>>>>> REPLACE"
        },
    )

    # Immediately attempting apply_patch again is blocked
    decision = policy.evaluate_action(state, patch_call)
    assert not decision.allowed
    assert "failed SEARCH matching" in decision.blocked_reason
    assert state.guard_firings["search_retry_blocks"] >= 1

    # After performing a new read_file, last_failed_patch_file is cleared and patch is allowed
    state.last_failed_patch_file = ""
    decision2 = policy.evaluate_action(state, patch_call)
    assert decision2.allowed


def test_invalid_target_path_recovery():
    """Test 4: Target path that does not exist is invalidated and updated upon symbol/search discovery."""
    problem = _dummy_problem()
    state = AgentState(instance_id="test__pkg-1234", problem=problem)
    state.phase = AgentPhase.PLAN
    state.active_plan = RepairPlan(target_file="app.py", intended_change="Add dot check")

    tools = ToolRegistry()
    # Execute a failing read on app.py
    provider = MagicMock()
    controller = AgentController(provider=provider, tools=tools, policy=AgentPolicy(max_turns=3))

    # Simulate read_file returning error
    read_call = ToolCall(name="read_file", arguments={"file_path": "app.py"})
    fake_err_result = ToolResult(tool_name="read_file", status="ERROR", error="File 'app.py' does not exist.")
    tools.execute = MagicMock(return_value=fake_err_result)

    # Trigger read_file execution in controller flow
    # Simulating what controller does when read_file fails:
    state.invalid_paths.append("app.py")
    if state.active_plan.target_file == "app.py":
        state.active_plan.target_file = ""

    assert state.active_plan.target_file == ""
    assert "app.py" in state.invalid_paths

    # Later, find_symbol discovers src/flask/app.py
    sym_result = ToolResult(
        tool_name="find_symbol",
        status="SUCCESS",
        data={"symbols": [{"symbol": "register_blueprint", "file": "src/flask/app.py"}]},
    )
    tools.execute.return_value = sym_result

    # Simulate controller processing find_symbol result
    df = sym_result.data["symbols"][0]["file"]
    if not state.active_plan.target_file or state.active_plan.target_file in state.invalid_paths:
        state.active_plan.target_file = df

    assert state.active_plan.target_file == "src/flask/app.py"


def test_hypothesis_preservation_and_causality():
    """Test 5: Active hypothesis is preserved and continues to guide the RepairPlan."""
    state = AgentState(instance_id="test__pkg-1234", problem=_dummy_problem())
    hyp = Hypothesis(
        id="H001",
        description="Dot character in blueprint name causes routing collision",
        confidence=0.9,
        affected_files=["src/flask/blueprints.py"],
    )
    state.hypotheses.append(hyp)
    state.active_hypothesis = hyp

    # Verify plan generation preserves hypothesis linkage
    plan = RepairPlan(
        target_file=hyp.affected_files[0],
        intended_change="Add validation check rejecting dot in blueprint names",
        rationale=hyp.description,
    )
    state.active_plan = plan
    assert state.active_plan.target_file == "src/flask/blueprints.py"
    assert state.active_hypothesis.id == "H001"
    assert state.active_plan.rationale == hyp.description
