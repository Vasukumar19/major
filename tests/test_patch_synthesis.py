"""Focused unit tests for PatchForge v0.3 Patch Synthesis Fidelity."""
import pytest
from unittest.mock import MagicMock

from patchforge.agent.controller import AgentController
from patchforge.agent.policy import AgentPolicy
from patchforge.agent.state import AgentPhase, AgentState
from patchforge.issue.problem import Problem
from patchforge.reasoning.hypothesis import Hypothesis
from patchforge.reasoning.plan import RepairPlan
from patchforge.repair.generator import parse_edits
from patchforge.tools.base import ToolCall, ToolRegistry, ToolResult
from patchforge.tools.editor import ApplyPatchTool


def _dummy_problem() -> Problem:
    return Problem(
        instance_id="psf__requests-1963",
        repo="psf/requests",
        base_commit="110048f9",
        problem_statement="Session.resolve_redirects copies original request method across redirects.",
    )


def test_valid_patch_action_recognized():
    """Test A: A valid patch action emitted by the model is correctly recognized and parsed."""
    controller = AgentController(provider=MagicMock(), tools=ToolRegistry())
    raw_response = """```json
{
  "thought": "Applying SEARCH/REPLACE fix to requests/sessions.py",
  "action": {
    "name": "apply_patch",
    "arguments": {
      "patch_text": "### requests/sessions.py\\n<<<<<<< SEARCH\\n        prepared_request.method = method\\n=======\\n        prepared_request.method = method\\n>>>>>>> REPLACE"
    }
  }
}
```"""
    thought, tool_call = controller._parse_model_action(raw_response)
    assert tool_call is not None
    assert tool_call.name == "apply_patch"
    assert "### requests/sessions.py" in tool_call.arguments["patch_text"]
    assert "prepared_request.method = method" in tool_call.arguments["patch_text"]


def test_valid_patch_reaches_patch_tool(tmp_path):
    """Test B: A valid apply_patch action reaches the patch tool and applies cleanly."""
    file_path = tmp_path / "requests" / "sessions.py"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text("def resolve():\n    prepared_request.method = method\n", encoding="utf-8")

    tool = ApplyPatchTool(repo_dir=str(tmp_path))
    patch_text = "### requests/sessions.py\n<<<<<<< SEARCH\n    prepared_request.method = method\n=======\n    prepared_request.method = 'GET'\n>>>>>>> REPLACE"
    res = tool.execute({"patch_text": patch_text})

    assert res.status == "SUCCESS"
    assert "prepared_request.method = 'GET'" in file_path.read_text(encoding="utf-8")


def test_malformed_actions_rejected_safely():
    """Test C: Malformed actions with invalid JSON or missing SEARCH block are rejected safely."""
    controller = AgentController(provider=MagicMock(), tools=ToolRegistry())
    raw_invalid = '{"thought": "Broken JSON", "action": {"name": "apply_patch", "arguments": '
    thought, tool_call = controller._parse_model_action(raw_invalid)
    assert tool_call is None

    tool = ApplyPatchTool()
    res = tool.execute({"patch_text": "Not a valid patch syntax"})
    assert res.status == "ERROR"
    assert "Could not parse any valid SEARCH/REPLACE blocks" in res.error


def test_conversational_reasoning_not_treated_as_patch():
    """Test D: Conversational reasoning without a patch is NOT silently treated as a successful patch."""
    controller = AgentController(provider=MagicMock(), tools=ToolRegistry())
    state = AgentState(instance_id="psf__requests-1963", problem=_dummy_problem())
    state.phase = AgentPhase.PATCH

    raw_reasoning = "To fix this bug, we should change requests/sessions.py line 500 to use GET on redirect."
    thought, tool_call = controller._parse_model_action(raw_reasoning)
    assert tool_call is None
    classification = controller._classify_model_output(thought, tool_call, raw_reasoning, state)
    assert classification != "DONE"
    assert state.best_patch is None


def test_search_requires_verified_source_context():
    """Test E: Exact SEARCH/REPLACE content still requires verified source context before execution."""
    policy = AgentPolicy()
    state = AgentState(instance_id="psf__requests-1963", problem=_dummy_problem())
    state.phase = AgentPhase.PATCH
    state.active_plan = RepairPlan(target_file="requests/sessions.py", intended_change="Fix redirect")

    patch_call = ToolCall(
        name="apply_patch",
        arguments={
            "patch_text": "### requests/sessions.py\n<<<<<<< SEARCH\n        prepared_request.method = method\n=======\n        prepared_request.method = 'GET'\n>>>>>>> REPLACE"
        },
    )

    # Without verified read, action is blocked
    decision = policy.evaluate_action(state, patch_call)
    assert not decision.allowed
    assert "inspecting its exact source context" in decision.blocked_reason

    # With verified read, action is allowed
    state.verified_read_files.append("requests/sessions.py")
    decision2 = policy.evaluate_action(state, patch_call)
    assert decision2.allowed


def test_hypothesis_and_plan_causality():
    """Test F: Hypothesis and RepairPlan remain intact and actively linked to the patch target."""
    state = AgentState(instance_id="psf__requests-1963", problem=_dummy_problem())
    hyp = Hypothesis(
        id="H001",
        description="Session.resolve_redirects copies original method incorrectly",
        confidence=0.85,
        affected_files=["requests/sessions.py"],
    )
    state.hypotheses.append(hyp)
    state.active_hypothesis = hyp

    plan = RepairPlan(
        target_file=hyp.affected_files[0],
        intended_change="Reset redirect method to GET on 303/302",
        rationale=hyp.description,
    )
    state.active_plan = plan

    assert state.active_plan.target_file == "requests/sessions.py"
    assert state.active_plan.rationale == hyp.description
    assert state.active_hypothesis.id == "H001"
