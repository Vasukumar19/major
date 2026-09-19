"""Unit tests for deterministic target context acquisition in AgentController."""
import pytest
from unittest.mock import MagicMock

from patchforge.agent.context import ContextCompactor
from patchforge.agent.controller import AgentController
from patchforge.agent.policy import AgentPolicy
from patchforge.agent.state import AgentPhase, AgentState
from patchforge.agent.trajectory import AgentTrajectory
from patchforge.issue.problem import Problem
from patchforge.reasoning.hypothesis import Hypothesis
from patchforge.reasoning.plan import RepairPlan
from patchforge.tools.base import ToolCall, ToolRegistry, ToolResult
from patchforge.tools.editor import ReadFileTool, ApplyPatchTool


def _dummy_problem() -> Problem:
    return Problem(
        instance_id="psf__requests-1963",
        repo="psf/requests",
        base_commit="110048f9",
        problem_statement="Session.resolve_redirects copies original request method across redirects.",
    )


def test_missing_context_automatically_acquired(tmp_path):
    """Test 1: Given phase = PLAN, target_file = requests/sessions.py, verified_read_files = [],
    the controller automatically obtains source context before PATCH."""
    target_f = tmp_path / "requests" / "sessions.py"
    target_f.parent.mkdir(parents=True, exist_ok=True)
    target_f.write_text("def resolve_redirects():\n    pass\n", encoding="utf-8")

    tools = ToolRegistry()
    tools.register(ReadFileTool(repo_dir=str(tmp_path)))

    controller = AgentController(provider=MagicMock(), tools=tools)
    state = AgentState(instance_id="psf__requests-1963", problem=_dummy_problem(), repo_dir=str(tmp_path))
    state.phase = AgentPhase.PATCH
    state.active_plan = RepairPlan(target_file="requests/sessions.py", intended_change="Fix redirect")
    trajectory = AgentTrajectory(instance_id=state.instance_id)

    assert "requests/sessions.py" not in state.verified_read_files

    # Execute deterministic context acquisition
    res = controller.acquire_target_context(state, trajectory)

    assert res is True
    assert "requests/sessions.py" in state.verified_read_files
    assert any(step.action_name == "read_file" and "requests/sessions.py" in step.observation_summary for step in trajectory.steps)


def test_existing_verified_context_not_reread(tmp_path):
    """Test 2: Given phase = PLAN, target_file already verified, controller does NOT unnecessarily reread the file."""
    target_f = tmp_path / "requests" / "sessions.py"
    target_f.parent.mkdir(parents=True, exist_ok=True)
    target_f.write_text("def resolve_redirects():\n    pass\n", encoding="utf-8")

    mock_read_tool = MagicMock()
    mock_read_tool.name = "read_file"
    mock_read_tool.parameters = {}
    tools = ToolRegistry()
    tools.register(mock_read_tool)

    controller = AgentController(provider=MagicMock(), tools=tools)
    state = AgentState(instance_id="psf__requests-1963", problem=_dummy_problem(), repo_dir=str(tmp_path))
    state.phase = AgentPhase.PATCH
    state.active_plan = RepairPlan(target_file="requests/sessions.py", intended_change="Fix redirect")
    state.verified_read_files.append("requests/sessions.py")
    trajectory = AgentTrajectory(instance_id=state.instance_id)

    res = controller.acquire_target_context(state, trajectory)

    assert res is True
    # Should not call tools.execute because file is already verified
    assert mock_read_tool.execute.call_count == 0


def test_read_failure_does_not_verify_context(tmp_path):
    """Test 3: If automatic read_file fails, do NOT mark the file verified.
    The model must not be allowed to patch against unverified context."""
    tools = ToolRegistry()
    tools.register(ReadFileTool(repo_dir=str(tmp_path)))  # File does not exist in tmp_path

    controller = AgentController(provider=MagicMock(), tools=tools)
    policy = AgentPolicy()
    state = AgentState(instance_id="psf__requests-1963", problem=_dummy_problem(), repo_dir=str(tmp_path))
    state.phase = AgentPhase.PATCH
    state.active_plan = RepairPlan(target_file="nonexistent/file.py", intended_change="Fix redirect")
    trajectory = AgentTrajectory(instance_id=state.instance_id)

    res = controller.acquire_target_context(state, trajectory)

    assert res is False
    assert "nonexistent/file.py" not in state.verified_read_files
    assert "nonexistent/file.py" in state.invalid_paths

    # Policy must block unverified patch attempt
    patch_call = ToolCall(
        name="apply_patch",
        arguments={"patch_text": "### nonexistent/file.py\n<<<<<<< SEARCH\nfoo\n=======\nbar\n>>>>>>> REPLACE"},
    )
    decision = policy.evaluate_action(state, patch_call)
    assert decision.allowed is False


def test_patch_remains_model_generated(tmp_path):
    """Test 4: After verified context exists, the controller must still wait for the model's apply_patch."""
    target_f = tmp_path / "requests" / "sessions.py"
    target_f.parent.mkdir(parents=True, exist_ok=True)
    target_f.write_text("def resolve():\n    return 1\n", encoding="utf-8")

    tools = ToolRegistry()
    tools.register(ReadFileTool(repo_dir=str(tmp_path)))
    tools.register(ApplyPatchTool(repo_dir=str(tmp_path)))

    mock_provider = MagicMock()
    mock_provider.generate_one.return_value = type("Resp", (), {
        "text": '```json\n{"thought": "Applying fix", "action": {"name": "apply_patch", "arguments": {"patch_text": "### requests/sessions.py\\n<<<<<<< SEARCH\\n    return 1\\n=======\\n    return 2\\n>>>>>>> REPLACE"}}}\n```',
        "input_tokens": 100,
        "output_tokens": 50,
        "cost_usd": 0.0,
    })()

    controller = AgentController(provider=mock_provider, tools=tools)
    state = AgentState(instance_id="psf__requests-1963", problem=_dummy_problem(), repo_dir=str(tmp_path), max_turns=1)
    state.phase = AgentPhase.PATCH
    state.active_plan = RepairPlan(target_file="requests/sessions.py", intended_change="Fix redirect")
    
    patch, traj, final_state = controller.run(problem=_dummy_problem(), repo_dir=str(tmp_path), initial_state=state)

    assert final_state.active_patch is not None
    assert final_state.active_patch.valid is True
    assert "+    return 2" in final_state.active_patch.patch_text


def test_hypothesis_and_plan_causality_preserved(tmp_path):
    """Test 5: Verify active_hypothesis -> RepairPlan -> target_file -> verified source -> apply_patch causality."""
    target_f = tmp_path / "requests" / "sessions.py"
    target_f.parent.mkdir(parents=True, exist_ok=True)
    target_f.write_text("def resolve():\n    method = 'POST'\n", encoding="utf-8")

    tools = ToolRegistry()
    tools.register(ReadFileTool(repo_dir=str(tmp_path)))
    tools.register(ApplyPatchTool(repo_dir=str(tmp_path)))

    mock_provider = MagicMock()
    mock_provider.generate_one.return_value = type("Resp", (), {
        "text": '```json\n{"thought": "Fixing method", "action": {"name": "apply_patch", "arguments": {"patch_text": "### requests/sessions.py\\n<<<<<<< SEARCH\\n    method = \'POST\'\\n=======\\n    method = \'GET\'\\n>>>>>>> REPLACE"}}}\n```',
        "input_tokens": 100,
        "output_tokens": 50,
        "cost_usd": 0.0,
    })()

    controller = AgentController(provider=mock_provider, tools=tools)
    state = AgentState(instance_id="psf__requests-1963", problem=_dummy_problem(), repo_dir=str(tmp_path), max_turns=1)
    hyp = Hypothesis(id="H1", description="Redirect method bug", affected_files=["requests/sessions.py"])
    state.hypotheses.append(hyp)
    state.active_hypothesis = hyp
    state.active_plan = RepairPlan(target_file="requests/sessions.py", intended_change="Set GET on redirect")
    state.phase = AgentPhase.PATCH

    patch, traj, final_state = controller.run(problem=_dummy_problem(), repo_dir=str(tmp_path), initial_state=state)

    assert final_state.active_hypothesis.id == "H1"
    assert final_state.active_plan.target_file == "requests/sessions.py"
    assert "requests/sessions.py" in final_state.verified_read_files
    assert final_state.active_patch is not None


def test_no_architecture_bypass(tmp_path):
    """Test 6: Verify that the intervention does not bypass the existing reasoning pipeline."""
    tools = ToolRegistry()
    tools.register(ReadFileTool(repo_dir=str(tmp_path)))

    controller = AgentController(provider=MagicMock(), tools=tools)
    state = AgentState(instance_id="psf__requests-1963", problem=_dummy_problem(), repo_dir=str(tmp_path))
    
    trajectory = AgentTrajectory(instance_id=state.instance_id)
    assert state.phase == AgentPhase.INVESTIGATE
    assert len(state.verified_read_files) == 0
    assert len(state.hypotheses) == 0
    assert state.active_plan is None
