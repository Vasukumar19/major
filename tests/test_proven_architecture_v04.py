"""Unit tests for PatchForge v0.4 Proven-Agent Architecture Recovery."""
import json
from pathlib import Path
from unittest.mock import MagicMock
import pytest

from patchforge.agent.context import ContextCompactor
from patchforge.agent.controller import AgentController, SYSTEM_PATCH_PROMPT
from patchforge.agent.policy import AgentPolicy, ActionDecision, PolicyDecision
from patchforge.agent.state import AgentPhase, AgentState
from patchforge.agent.trajectory import AgentTrajectory
from patchforge.issue.problem import Problem
from patchforge.models.provider import Generation
from patchforge.reasoning.hypothesis import Hypothesis
from patchforge.reasoning.plan import RepairPlan
from patchforge.repair.patch import Patch
from patchforge.tools import create_default_tool_registry
from patchforge.tools.base import ToolCall, ToolRegistry, ToolResult
from patchforge.tools.editor import ApplyPatchTool


def _dummy_problem(instance_id="p-1"):
    return Problem(
        instance_id=instance_id,
        problem_statement="Requests drop HTTP redirect method across 303 responses.",
        repo="psf/requests",
        base_commit="abc1234",
    )


def test_linear_progression_investigate_to_hypothesize(tmp_path):
    """Test 1: Linear progression - bounded investigation advances to HYPOTHESIZE after 2 turns."""
    repo_file = tmp_path / "requests" / "models.py"
    repo_file.parent.mkdir(parents=True, exist_ok=True)
    repo_file.write_text("class Request:\n    pass\n", encoding="utf-8")

    tools = create_default_tool_registry(repo_dir=str(tmp_path))
    mock_provider = MagicMock()
    # Turn 1: read file
    r1 = Generation(
        text=json.dumps({"thought": "Read file", "action": {"name": "read_file", "arguments": {"file_path": "requests/models.py"}}}),
        input_tokens=10, output_tokens=10, cost_usd=0.0, model="dummy",
    )
    # Turn 2: read again
    r2 = Generation(
        text=json.dumps({"thought": "Read again", "action": {"name": "read_file", "arguments": {"file_path": "requests/models.py"}}}),
        input_tokens=10, output_tokens=10, cost_usd=0.0, model="dummy",
    )
    # Turn 3: finishes
    r3 = Generation(
        text=json.dumps({"thought": "Finished", "action": {"name": "done", "arguments": {}}}),
        input_tokens=10, output_tokens=10, cost_usd=0.0, model="dummy",
    )
    mock_provider.generate_one.side_effect = [
        Generation(text="{}", input_tokens=5, output_tokens=5, cost_usd=0.0, model="dummy"),
        r1, r2, r3,
    ]

    controller = AgentController(provider=mock_provider, tools=tools, policy=AgentPolicy(max_turns=5))
    patch, traj, state = controller.run(_dummy_problem(), repo_dir=str(tmp_path))
    assert state.localization_turn_count >= 2
    assert state.phase in (AgentPhase.HYPOTHESIZE, AgentPhase.PLAN, AgentPhase.PATCH, AgentPhase.DONE)


def test_target_grounding_auto_corrects_drifted_filename(tmp_path):
    """Test 2: Target grounding - auto-corrects 'requests.py' to verified 'requests/models.py'."""
    repo_file = tmp_path / "requests" / "models.py"
    repo_file.parent.mkdir(parents=True, exist_ok=True)
    repo_file.write_text("class Request:\n    def __init__(self):\n        self.method = None\n", encoding="utf-8")

    tool = ApplyPatchTool(repo_dir=str(tmp_path))
    # Drifted patch references requests.py instead of requests/models.py
    drifted_patch = (
        "### requests.py\n"
        "<<<<<<< SEARCH\n"
        "        self.method = None\n"
        "=======\n"
        "        self.method = 'GET'\n"
        ">>>>>>> REPLACE"
    )
    res = tool.execute({"patch_text": drifted_patch})
    assert res.status == "SUCCESS"
    assert "requests/models.py" in res.data["files_changed"]
    assert "self.method = 'GET'" in repo_file.read_text(encoding="utf-8")


def test_compact_patch_context_contains_only_repair_state():
    """Test 3: Compact PATCH context contains ONLY repair state, stripped of rambling transcripts."""
    compactor = ContextCompactor()
    state = AgentState(instance_id="p-1", problem=_dummy_problem(), turn_count=4, max_turns=12)
    state.phase = AgentPhase.PATCH
    state.active_plan = RepairPlan(
        target_file="requests/sessions.py",
        target_symbols=["resolve_redirects"],
        intended_change="Reset method to GET on 303",
    )
    state.pinned_snippets["requests/sessions.py"] = "def resolve_redirects():\n    method = req.method\n"
    state.verified_read_files.append("requests/sessions.py")

    traj = AgentTrajectory(instance_id=state.instance_id)
    # Add rambling historical steps
    traj.record_step(turn=1, phase="INVESTIGATE", thought="Exploring files rambling", tool_call=None, tool_result=None)
    traj.record_step(turn=2, phase="INVESTIGATE", thought="Still exploring rambling", tool_call=None, tool_result=None)

    prompt = compactor.compact_prompt(state, traj)
    assert "=== CURRENT AGENT PHASE: PATCH" in prompt
    assert "=== PATCH CONTRACT ===" in prompt
    assert "=== REPAIR STATE ===" in prompt
    assert "Target File: requests/sessions.py" in prompt
    assert "=== VERIFIED SOURCE CONTEXT (VERBATIM) ===" in prompt
    # Ensure rambling history is NOT in the PATCH prompt
    assert "Exploring files rambling" not in prompt
    assert "Still exploring rambling" not in prompt


def test_no_infinite_search_in_plan_or_patch():
    """Test 4: Policy blocks search_code in PLAN/PATCH and forces transition to PATCH."""
    policy = AgentPolicy()
    state = AgentState(instance_id="p-1", problem=_dummy_problem())
    state.phase = AgentPhase.PLAN

    call = ToolCall(name="search_code", arguments={"query": "def resolve_redirects"})
    decision = policy.evaluate_action(state, call)
    assert decision.allowed is False
    assert decision.next_phase == AgentPhase.PATCH
    assert "BLOCKED" in decision.blocked_reason


def test_no_infinite_prose_loop_in_patch(tmp_path):
    """Test 5: Controller breaks prose deadlock via direct model patch query fallback."""
    repo_file = tmp_path / "requests" / "sessions.py"
    repo_file.parent.mkdir(parents=True, exist_ok=True)
    repo_file.write_text("def resolve_redirects():\n    method = 'POST'\n", encoding="utf-8")

    tools = create_default_tool_registry(repo_dir=str(tmp_path))
    mock_provider = MagicMock()

    # Turn 1: Model reads file
    r1 = Generation(
        text=json.dumps({"thought": "Read file", "action": {"name": "read_file", "arguments": {"file_path": "requests/sessions.py"}}}),
        input_tokens=10, output_tokens=10, cost_usd=0.0, model="dummy",
    )
    # Turn 2: Model outputs prose in PATCH
    r2 = Generation(
        text="### Repair Plan\nI will modify requests/sessions.py to fix redirect method.",
        input_tokens=10, output_tokens=10, cost_usd=0.0, model="dummy",
    )
    # Turn 3: Model outputs prose again, but fallback query returns SEARCH/REPLACE
    r3 = Generation(
        text="Still explaining the fix in prose.",
        input_tokens=10, output_tokens=10, cost_usd=0.0, model="dummy",
    )
    r_direct = Generation(
        text=(
            "### requests/sessions.py\n"
            "<<<<<<< SEARCH\n"
            "    method = 'POST'\n"
            "=======\n"
            "    method = 'GET'\n"
            ">>>>>>> REPLACE"
        ),
        input_tokens=10, output_tokens=10, cost_usd=0.0, model="dummy",
    )

    mock_provider.generate_one.side_effect = [
        Generation(text="{}", input_tokens=5, output_tokens=5, cost_usd=0.0, model="dummy"),
        r1, r2, r3, r_direct,
    ]

    controller = AgentController(provider=mock_provider, tools=tools, policy=AgentPolicy(max_turns=6))
    state = AgentState(instance_id="p-1", problem=_dummy_problem(), repo_dir=str(tmp_path))
    state.phase = AgentPhase.PATCH
    state.active_plan = RepairPlan(target_file="requests/sessions.py", intended_change="Change POST to GET")
    state.verified_read_files.append("requests/sessions.py")
    state.pinned_snippets["requests/sessions.py"] = "def resolve_redirects():\n    method = 'POST'\n"

    patch, traj, final_state = controller.run(_dummy_problem(), repo_dir=str(tmp_path), initial_state=state)
    assert patch is not None
    assert patch.valid is True
    assert final_state.best_score == 1.0


def test_test_failure_transitions_to_refine():
    """Test 6: Test failure transitions to REFINE for iterative repair instead of abandoning to INVESTIGATE."""
    policy = AgentPolicy()
    state = AgentState(instance_id="p-1", problem=_dummy_problem())
    decision = policy.evaluate_execution_result(
        state, passed=False, fail_to_pass_passed=0, fail_to_pass_total=2, p2p_regressions=0
    )
    assert decision.should_terminate is False
    assert decision.next_phase == AgentPhase.REFINE
    assert "Transitioning to REFINE" in decision.reason
