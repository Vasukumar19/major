"""Unit tests for deterministic patch action selection and format contract."""
import pytest
from unittest.mock import MagicMock
from pathlib import Path

from patchforge.agent.context import ContextCompactor
from patchforge.agent.controller import AgentController
from patchforge.agent.policy import AgentPolicy
from patchforge.agent.state import AgentPhase, AgentState
from patchforge.agent.trajectory import AgentTrajectory
from patchforge.issue.problem import Problem
from patchforge.reasoning.hypothesis import Hypothesis
from patchforge.reasoning.plan import RepairPlan
from patchforge.repair.generator import parse_edits
from patchforge.tools.base import ToolCall, ToolRegistry, ToolResult
from patchforge.tools.editor import ReadFileTool, ApplyPatchTool


def _dummy_problem() -> Problem:
    return Problem(
        instance_id="psf__requests-1963",
        repo="psf/requests",
        base_commit="110048f9",
        problem_statement="Session.resolve_redirects copies original request method across redirects.",
    )


def test_patch_contract_present_in_context():
    """Test 1: Verify PATCH context contains the required apply_patch contract instructions."""
    compactor = ContextCompactor()
    state = AgentState(instance_id="psf__requests-1963", problem=_dummy_problem())
    state.phase = AgentPhase.PATCH
    state.active_plan = RepairPlan(target_file="requests/sessions.py", intended_change="Fix redirect")
    trajectory = AgentTrajectory(instance_id=state.instance_id)

    prompt = compactor.compact_prompt(state, trajectory)
    assert "=== PATCH CONTRACT ===" in prompt
    assert "apply_patch" in prompt
    assert "SEARCH/REPLACE" in prompt
    assert "diff --git" in prompt


def test_prose_classified_as_prose_no_tool():
    """Test 2: Conversational prose without a tool call is classified as PROSE_NO_TOOL."""
    controller = AgentController(provider=MagicMock(), tools=ToolRegistry())
    state = AgentState(instance_id="psf__requests-1963", problem=_dummy_problem())
    state.phase = AgentPhase.PATCH

    raw_text = "Here is the fix: we should change line 10 to reset the method."
    thought, tool_call = controller._parse_model_action(raw_text)
    cat = controller.classify_patch_output(thought, tool_call, raw_text, state)
    assert cat == "PROSE_NO_TOOL"


def test_unified_diff_rejected_as_wrong_patch_format():
    """Test 3: Unified diff is rejected with WRONG_PATCH_FORMAT."""
    controller = AgentController(provider=MagicMock(), tools=ToolRegistry())
    policy = AgentPolicy()
    state = AgentState(instance_id="psf__requests-1963", problem=_dummy_problem())
    state.phase = AgentPhase.PATCH
    state.verified_read_files.append("requests/sessions.py")

    diff_text = """```json
{
  "thought": "Applying diff",
  "action": {
    "name": "apply_patch",
    "arguments": {
      "patch_text": "diff --git a/requests/sessions.py b/requests/sessions.py\\n--- a/requests/sessions.py\\n+++ b/requests/sessions.py\\n@@ -10,1 +10,1 @@\\n-old\\n+new"
    }
  }
}
```"""
    thought, tool_call = controller._parse_model_action(diff_text)
    cat = controller.classify_patch_output(thought, tool_call, diff_text, state)
    assert cat == "WRONG_PATCH_FORMAT"

    decision = policy.evaluate_action(state, tool_call)
    assert decision.allowed is False
    assert "WRONG_PATCH_FORMAT" in decision.blocked_reason


def test_valid_search_replace_accepted(tmp_path):
    """Test 4: A valid SEARCH/REPLACE patch is accepted as VALID_PATCH."""
    target_f = tmp_path / "requests" / "sessions.py"
    target_f.parent.mkdir(parents=True, exist_ok=True)
    target_f.write_text("def resolve():\n    method = 'POST'\n", encoding="utf-8")

    controller = AgentController(provider=MagicMock(), tools=ToolRegistry())
    state = AgentState(instance_id="psf__requests-1963", problem=_dummy_problem(), repo_dir=str(tmp_path))
    state.phase = AgentPhase.PATCH
    state.verified_read_files.append("requests/sessions.py")

    patch_call = ToolCall(
        name="apply_patch",
        arguments={
            "patch_text": "### requests/sessions.py\n<<<<<<< SEARCH\n    method = 'POST'\n=======\n    method = 'GET'\n>>>>>>> REPLACE"
        },
    )
    cat = controller.classify_patch_output("Applying patch", patch_call, "", state)
    assert cat == "VALID_PATCH"


def test_unverified_file_rejected(tmp_path):
    """Test 5: A patch targeting an unverified file is rejected with UNVERIFIED_FILE."""
    controller = AgentController(provider=MagicMock(), tools=ToolRegistry())
    policy = AgentPolicy()
    state = AgentState(instance_id="psf__requests-1963", problem=_dummy_problem(), repo_dir=str(tmp_path))
    state.phase = AgentPhase.PATCH
    # File is not in state.verified_read_files

    patch_call = ToolCall(
        name="apply_patch",
        arguments={
            "patch_text": "### requests/sessions.py\n<<<<<<< SEARCH\n    method = 'POST'\n=======\n    method = 'GET'\n>>>>>>> REPLACE"
        },
    )
    cat = controller.classify_patch_output("Applying patch", patch_call, "", state)
    assert cat == "UNVERIFIED_FILE"

    decision = policy.evaluate_action(state, patch_call)
    assert decision.allowed is False
    assert "UNVERIFIED_FILE" in decision.blocked_reason


def test_search_grounding_rejects_hallucinated_lines(tmp_path):
    """Test 6: SEARCH text absent from verified source is rejected with SEARCH_NOT_FOUND."""
    target_f = tmp_path / "requests" / "sessions.py"
    target_f.parent.mkdir(parents=True, exist_ok=True)
    target_f.write_text("def resolve():\n    method = 'POST'\n", encoding="utf-8")

    controller = AgentController(provider=MagicMock(), tools=ToolRegistry())
    policy = AgentPolicy()
    state = AgentState(instance_id="psf__requests-1963", problem=_dummy_problem(), repo_dir=str(tmp_path))
    state.phase = AgentPhase.PATCH
    state.verified_read_files.append("requests/sessions.py")

    patch_call = ToolCall(
        name="apply_patch",
        arguments={
            "patch_text": "### requests/sessions.py\n<<<<<<< SEARCH\n    non_existent_code_line = 12345\n=======\n    method = 'GET'\n>>>>>>> REPLACE"
        },
    )
    cat = controller.classify_patch_output("Applying patch", patch_call, "", state)
    assert cat == "SEARCH_NOT_FOUND"

    decision = policy.evaluate_action(state, patch_call)
    assert decision.allowed is False
    assert "SEARCH_NOT_FOUND" in decision.blocked_reason


def test_model_remains_patch_author(tmp_path):
    """Test 7: Verify controller does not generate replacement code automatically."""
    target_f = tmp_path / "requests" / "sessions.py"
    target_f.parent.mkdir(parents=True, exist_ok=True)
    target_f.write_text("def resolve():\n    return 1\n", encoding="utf-8")

    tools = ToolRegistry()
    tools.register(ReadFileTool(repo_dir=str(tmp_path)))
    tools.register(ApplyPatchTool(repo_dir=str(tmp_path)))

    # Provider returns prose (no patch)
    mock_provider = MagicMock()
    mock_provider.generate_one.return_value = type("Resp", (), {
        "text": "The bug should be fixed by returning 2 instead of 1.",
        "input_tokens": 100,
        "output_tokens": 50,
        "cost_usd": 0.0,
    })()

    controller = AgentController(provider=mock_provider, tools=tools)
    state = AgentState(instance_id="psf__requests-1963", problem=_dummy_problem(), repo_dir=str(tmp_path), max_turns=1)
    state.phase = AgentPhase.PATCH
    state.active_plan = RepairPlan(target_file="requests/sessions.py", intended_change="Return 2")

    patch, traj, final_state = controller.run(problem=_dummy_problem(), repo_dir=str(tmp_path), initial_state=state)

    # Controller must NOT synthesize a patch from the model's prose
    assert final_state.active_patch is None
    assert final_state.best_patch is None


def test_existing_patch_engine_preserved(tmp_path):
    """Test 8: Valid model-generated patch flows through the existing ApplyPatchTool."""
    target_f = tmp_path / "requests" / "sessions.py"
    target_f.parent.mkdir(parents=True, exist_ok=True)
    target_f.write_text("def resolve():\n    return 1\n", encoding="utf-8")

    tool = ApplyPatchTool(repo_dir=str(tmp_path))
    res = tool.execute({
        "patch_text": "### requests/sessions.py\n<<<<<<< SEARCH\n    return 1\n=======\n    return 2\n>>>>>>> REPLACE"
    })
    assert res.status == "SUCCESS"
    assert target_f.read_text(encoding="utf-8") == "def resolve():\n    return 2\n"


def test_recovery_after_malformed_patch(tmp_path):
    """Test 9: Malformed patch returns control to the model with blocked reason."""
    target_f = tmp_path / "requests" / "sessions.py"
    target_f.parent.mkdir(parents=True, exist_ok=True)
    target_f.write_text("def resolve():\n    return 1\n", encoding="utf-8")

    tools = ToolRegistry()
    tools.register(ReadFileTool(repo_dir=str(tmp_path)))
    tools.register(ApplyPatchTool(repo_dir=str(tmp_path)))

    # Turn 1: model returns prose; Turn 2: model returns valid patch
    mock_provider = MagicMock()
    mock_provider.generate_one.side_effect = [
        type("Resp", (), {
            "text": "I think we should change return 1 to return 2.",
            "input_tokens": 100,
            "output_tokens": 50,
            "cost_usd": 0.0,
        })(),
        type("Resp", (), {
            "text": '```json\n{"thought": "Fixing code", "action": {"name": "apply_patch", "arguments": {"patch_text": "### requests/sessions.py\\n<<<<<<< SEARCH\\n    return 1\\n=======\\n    return 2\\n>>>>>>> REPLACE"}}}\n```',
            "input_tokens": 100,
            "output_tokens": 50,
            "cost_usd": 0.0,
        })(),
    ]

    controller = AgentController(provider=mock_provider, tools=tools)
    state = AgentState(instance_id="psf__requests-1963", problem=_dummy_problem(), repo_dir=str(tmp_path), max_turns=2)
    state.phase = AgentPhase.PATCH
    state.active_plan = RepairPlan(target_file="requests/sessions.py", intended_change="Return 2")

    patch, traj, final_state = controller.run(problem=_dummy_problem(), repo_dir=str(tmp_path), initial_state=state)

    assert final_state.active_patch is not None
    assert final_state.active_patch.valid is True
    assert target_f.read_text(encoding="utf-8") == "def resolve():\n    return 2\n"


def test_deterministic_context_tests_continue_passing(tmp_path):
    """Test 10: Verify existing deterministic context acquisition logic remains fully operational."""
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

    res = controller.acquire_target_context(state, trajectory)
    assert res is True
    assert "requests/sessions.py" in state.verified_read_files
