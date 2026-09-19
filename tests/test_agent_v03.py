"""Unit tests for AgentController, Policy, and Trajectory in PatchForge v0.3."""
import json
from unittest.mock import MagicMock
import pytest

from patchforge.agent.controller import AgentController
from patchforge.agent.policy import AgentPolicy, PolicyDecision
from patchforge.agent.state import AgentPhase, AgentState
from patchforge.agent.trajectory import AgentTrajectory
from patchforge.issue.problem import Problem
from patchforge.models.provider import Generation
from patchforge.repair.patch import Patch
from patchforge.tools import create_default_tool_registry


def test_agent_policy_turn_limit():
    policy = AgentPolicy(max_turns=3, max_cost_usd=1.0)
    prob = Problem(instance_id="p-1", problem_statement="test", repo="test", base_commit="abc")
    state = AgentState(instance_id="p-1", problem=prob, turn_count=3)

    decision = policy.evaluate_budget(state)
    assert decision.should_terminate is True
    assert decision.next_phase == AgentPhase.FAILED
    assert "maximum turn limit" in decision.reason


def test_agent_policy_near_miss_transition():
    policy = AgentPolicy()
    prob = Problem(instance_id="p-1", problem_statement="test", repo="test", base_commit="abc")
    state = AgentState(instance_id="p-1", problem=prob)

    # 1/2 tests passed (50%), 0 regressions
    decision = policy.evaluate_execution_result(
        state, passed=False, fail_to_pass_passed=1, fail_to_pass_total=2, p2p_regressions=0
    )
    assert decision.should_terminate is False
    assert decision.next_phase == AgentPhase.REFINE
    assert "Near-miss detected" in decision.reason


def test_agent_trajectory_recording():
    traj = AgentTrajectory(instance_id="inst-1")
    traj.record_step(
        turn=1,
        phase="INVESTIGATE",
        thought="Need to search for symbol",
        tool_call=None,
        tool_result=None,
    )
    assert len(traj.steps) == 1
    assert traj.steps[0].phase == "INVESTIGATE"
    traj.finish(verdict="Completed", resolved=True)
    assert traj.resolved is True
    d = traj.to_dict()
    assert d["final_verdict"] == "Completed"


def test_agent_controller_step_execution(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "core.py").write_text("def run():\n    return 42\n", encoding="utf-8")

    prob = Problem(instance_id="p-1", problem_statement="Fix run return", repo="test", base_commit="abc")
    tools = create_default_tool_registry(repo_dir=str(tmp_path))

    mock_provider = MagicMock()
    # Step 1: LLM searches code
    resp1 = Generation(
        text=json.dumps({
            "thought": "Let's search for run",
            "action": {"name": "search_code", "arguments": {"query": "def run"}},
        }),
        input_tokens=10, output_tokens=10, cost_usd=0.001, model="dummy",
    )
    # Step 2: LLM finishes
    resp2 = Generation(
        text=json.dumps({
            "thought": "Done investigating",
            "action": {"name": "done", "arguments": {}},
        }),
        input_tokens=10, output_tokens=10, cost_usd=0.001, model="dummy",
    )
    mock_provider.generate_one.side_effect = [
        Generation(text="{}", input_tokens=5, output_tokens=5, cost_usd=0.0, model="dummy"),  # for derive_understanding
        resp1,
        resp2,
    ]

    controller = AgentController(
        provider=mock_provider,
        tools=tools,
        policy=AgentPolicy(max_turns=5),
        model="dummy",
    )

    patch, traj, state = controller.run(prob, repo_dir=str(tmp_path))
    assert state.turn_count >= 1
    assert len(traj.steps) >= 1
    assert state.phase == AgentPhase.DONE


def test_reasoning_only_progression_without_abortion(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "core.py").write_text("def compute():\n    return 0\n", encoding="utf-8")

    prob = Problem(instance_id="p-2", problem_statement="Fix compute", repo="test", base_commit="abc")
    tools = create_default_tool_registry(repo_dir=str(tmp_path))

    mock_provider = MagicMock()
    # Turn 1: Model reads file
    resp1 = Generation(
        text=json.dumps({
            "thought": "Read core.py",
            "action": {"name": "read_file", "arguments": {"file_path": "pkg/core.py"}},
        }),
        input_tokens=10, output_tokens=10, cost_usd=0.001, model="dummy",
    )
    # Turn 2: Model outputs reasoning-only (no tool call)
    resp2 = Generation(
        text=json.dumps({
            "thought": "The root cause is that compute returns 0 instead of 1.",
        }),
        input_tokens=10, output_tokens=10, cost_usd=0.001, model="dummy",
    )
    # Turn 3: Model outputs plan reasoning (no tool call)
    resp3 = Generation(
        text=json.dumps({
            "thought": "Plan is to replace return 0 with return 1 in pkg/core.py.",
        }),
        input_tokens=10, output_tokens=10, cost_usd=0.001, model="dummy",
    )
    # Turn 4: Model applies patch
    resp4 = Generation(
        text=json.dumps({
            "thought": "Applying patch now",
            "action": {
                "name": "apply_patch",
                "arguments": {
                    "patch_text": "### pkg/core.py\n<<<<<<< SEARCH\n    return 0\n=======\n    return 1\n>>>>>>> REPLACE",
                },
            },
        }),
        input_tokens=10, output_tokens=10, cost_usd=0.001, model="dummy",
    )

    mock_provider.generate_one.side_effect = [
        Generation(text="{}", input_tokens=5, output_tokens=5, cost_usd=0.0, model="dummy"),  # derive_understanding
        resp1,
        resp2,
        resp3,
        resp4,
    ]

    controller = AgentController(
        provider=mock_provider,
        tools=tools,
        policy=AgentPolicy(max_turns=10),
        model="dummy",
    )

    patch, traj, state = controller.run(prob, repo_dir=str(tmp_path))
    assert patch is not None
    assert state.best_patch is not None
    assert len(state.hypotheses) >= 1
    assert state.active_plan is not None
    assert state.phase == AgentPhase.DONE
    assert traj.resolved is True


def test_failed_tests_transition_to_diagnose(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "core.py").write_text("def compute():\n    return 0\n", encoding="utf-8")

    prob = Problem(instance_id="p-3", problem_statement="Fix compute", repo="test", base_commit="abc")
    tools = create_default_tool_registry(repo_dir=str(tmp_path))

    mock_provider = MagicMock()
    # Turn 1: Model reads file
    resp1 = Generation(
        text=json.dumps({
            "thought": "Read core.py",
            "action": {"name": "read_file", "arguments": {"file_path": "pkg/core.py"}},
        }),
        input_tokens=10, output_tokens=10, cost_usd=0.001, model="dummy",
    )
    # Turn 2: Model applies candidate patch
    resp2 = Generation(
        text=json.dumps({
            "thought": "Applying patch",
            "action": {
                "name": "apply_patch",
                "arguments": {
                    "patch_text": "### pkg/core.py\n<<<<<<< SEARCH\n    return 0\n=======\n    return 2\n>>>>>>> REPLACE",
                },
            },
        }),
        input_tokens=10, output_tokens=10, cost_usd=0.001, model="dummy",
    )
    # Turn 3: In DIAGNOSE phase, model concludes or attempts next turn
    resp3 = Generation(
        text=json.dumps({
            "thought": "Diagnosis concluded, finish",
            "action": {"name": "done", "arguments": {}},
        }),
        input_tokens=10, output_tokens=10, cost_usd=0.001, model="dummy",
    )

    mock_provider.generate_one.side_effect = [
        Generation(text="{}", input_tokens=5, output_tokens=5, cost_usd=0.0, model="dummy"),
        resp1,
        resp2,
        resp3,
    ]

    mock_tester = MagicMock()
    # Mock test execution failing
    mock_tester.run.return_value = MagicMock(
        resolved=False,
        fail_to_pass_passed=0,
        fail_to_pass_total=1,
        pass_to_pass_regressions=0,
        failed_tests=["test_compute"],
        test_output="AssertionError: assert 2 == 1",
    )

    controller = AgentController(
        provider=mock_provider,
        tools=tools,
        policy=AgentPolicy(max_turns=10, max_patch_attempts=3),
        model="dummy",
    )

    patch, traj, state = controller.run(prob, repo_dir=str(tmp_path), tester=mock_tester)
    assert len(state.patch_attempts) == 1
    assert state.last_execution_evidence is not None
    assert "AssertionError" in state.last_execution_evidence.traceback


def test_independent_phase_budget_enforcement():
    policy = AgentPolicy(max_hypothesize_turns=2, max_plan_turns=2)
    prob = Problem(instance_id="p-4", problem_statement="test", repo="test", base_commit="abc")
    
    # Hypothesize budget exceeded
    state = AgentState(instance_id="p-4", problem=prob, phase=AgentPhase.HYPOTHESIZE)
    state.record_phase_turn(AgentPhase.HYPOTHESIZE)
    state.record_phase_turn(AgentPhase.HYPOTHESIZE)
    
    decision = policy.evaluate_budget(state)
    assert decision.next_phase == AgentPhase.PLAN
    assert "Hypothesis phase turn limit" in decision.reason

    # Plan budget exceeded
    state2 = AgentState(instance_id="p-4", problem=prob, phase=AgentPhase.PLAN)
    state2.record_phase_turn(AgentPhase.PLAN)
    state2.record_phase_turn(AgentPhase.PLAN)
    
    decision2 = policy.evaluate_budget(state2)
    assert decision2.next_phase == AgentPhase.PATCH
    assert "Plan phase turn limit" in decision2.reason


def test_invalid_reasoning_does_not_become_fake_hypothesis(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "core.py").write_text("def compute():\n    return 0\n", encoding="utf-8")

    prob = Problem(instance_id="p-5", problem_statement="Fix compute", repo="test", base_commit="abc")
    tools = create_default_tool_registry(repo_dir=str(tmp_path))

    mock_provider = MagicMock()
    # Turn 1: Model outputs empty reasoning with no files visited
    resp1 = Generation(
        text=json.dumps({"thought": "Just thinking..."}),
        input_tokens=10, output_tokens=10, cost_usd=0.001, model="dummy",
    )
    # Turn 2: Conclude
    resp2 = Generation(
        text=json.dumps({"thought": "Done", "action": {"name": "done", "arguments": {}}}),
        input_tokens=10, output_tokens=10, cost_usd=0.001, model="dummy",
    )
    mock_provider.generate_one.side_effect = [
        Generation(text="{}", input_tokens=5, output_tokens=5, cost_usd=0.0, model="dummy"),
        resp1,
        resp2,
    ]

    controller = AgentController(
        provider=mock_provider,
        tools=tools,
        policy=AgentPolicy(max_turns=5),
        model="dummy",
    )

    patch, traj, state = controller.run(prob, repo_dir=str(tmp_path))
    assert len(state.hypotheses) == 0


