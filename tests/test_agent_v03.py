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
