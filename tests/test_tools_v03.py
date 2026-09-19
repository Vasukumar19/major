"""Unit tests for PatchForge v0.3 Tool System."""
import tempfile
from pathlib import Path
import pytest

from patchforge.tools.base import ToolCall, ToolRegistry, ToolResult
from patchforge.tools.editor import ApplyPatchTool, GitDiffTool, ReadFileTool, ReadTestTool
from patchforge.tools.execution import InspectFailureTool, RunReproductionTool, RunTargetedTestTool
from patchforge.tools.graph import InspectGitHistoryTool, InspectGraphTool
from patchforge.tools.search import FindReferencesTool, FindSymbolTool, SearchCodeTool, SearchExactTool
from patchforge.tools import create_default_tool_registry


@pytest.fixture
def sample_repo(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    
    code = (
        "def compute_total(price, tax):\n"
        "    # calculate total\n"
        "    if price < 0:\n"
        "        raise ValueError('price cannot be negative')\n"
        "    return price + (price * tax)\n"
    )
    (pkg / "calc.py").write_text(code, encoding="utf-8")

    test_code = (
        "from pkg.calc import compute_total\n"
        "def test_compute_total():\n"
        "    assert compute_total(100, 0.1) == 110\n"
    )
    (pkg / "test_calc.py").write_text(test_code, encoding="utf-8")
    return tmp_path


def test_tool_registry_initialization(sample_repo):
    reg = create_default_tool_registry(repo_dir=str(sample_repo))
    assert len(reg.list_tools()) == 16
    assert reg.get("search_code") is not None
    assert reg.get("apply_patch") is not None
    assert reg.get("formulate_hypothesis") is not None
    assert reg.get("propose_plan") is not None
    assert reg.get("specify_repair") is not None
    assert len(reg.schemas()) == 16


def test_search_code_tool(sample_repo):
    tool = SearchCodeTool(repo_dir=str(sample_repo))
    res = tool.execute({"query": "compute_total"})
    assert res.status == "SUCCESS"
    assert res.data["total_matches"] >= 2


def test_search_exact_tool(sample_repo):
    tool = SearchExactTool(repo_dir=str(sample_repo))
    res = tool.execute({"literal": "price cannot be negative"})
    assert res.status == "SUCCESS"
    assert res.data["total_matches"] == 1
    assert res.data["matches"][0]["file"] == "pkg/calc.py"


def test_find_symbol_tool(sample_repo):
    tool = FindSymbolTool(repo_dir=str(sample_repo))
    res = tool.execute({"symbol_name": "compute_total"})
    assert res.status == "SUCCESS"
    assert len(res.data["symbols"]) >= 1
    assert res.data["symbols"][0]["symbol"] == "compute_total"


def test_find_references_tool(sample_repo):
    tool = FindReferencesTool(repo_dir=str(sample_repo))
    res = tool.execute({"symbol_name": "compute_total"})
    assert res.status == "SUCCESS"
    assert len(res.data["references"]) >= 2


def test_read_file_tool(sample_repo):
    tool = ReadFileTool(repo_dir=str(sample_repo))
    res = tool.execute({"file_path": "pkg/calc.py", "start_line": 1, "end_line": 3})
    assert res.status == "SUCCESS"
    assert "1: def compute_total" in res.data["content"]
    assert res.data["start_line"] == 1
    assert res.data["end_line"] == 3


def test_read_test_tool(sample_repo):
    tool = ReadTestTool(repo_dir=str(sample_repo))
    res = tool.execute({"test_path": "pkg/test_calc.py", "test_name": "test_compute_total"})
    assert res.status == "SUCCESS"
    assert "def test_compute_total" in res.data["content"]


def test_apply_patch_tool_success(sample_repo):
    tool = ApplyPatchTool(repo_dir=str(sample_repo))
    patch_text = (
        "### pkg/calc.py\n"
        "<<<<<<< SEARCH\n"
        "    if price < 0:\n"
        "        raise ValueError('price cannot be negative')\n"
        "=======\n"
        "    if price <= 0:\n"
        "        raise ValueError('price must be positive')\n"
        ">>>>>>> REPLACE"
    )
    res = tool.execute({"patch_text": patch_text, "hypothesis_id": "H1"})
    assert res.status == "SUCCESS"
    assert "pkg/calc.py" in res.data["files_changed"]
    assert "price must be positive" in res.data["diff"]


def test_apply_patch_tool_ast_rejection(sample_repo):
    tool = ApplyPatchTool(repo_dir=str(sample_repo))
    broken_patch = (
        "### pkg/calc.py\n"
        "<<<<<<< SEARCH\n"
        "    return price + (price * tax)\n"
        "=======\n"
        "    return price + (...\n"
        ">>>>>>> REPLACE"
    )
    res = tool.execute({"patch_text": broken_patch})
    assert res.status == "ERROR"
    assert "AST Syntax validation failed" in res.error


def test_apply_patch_tool_search_rejection_uses_shared_matcher(sample_repo):
    tool = ApplyPatchTool(repo_dir=str(sample_repo))
    patch_text = (
        "### pkg/calc.py\n"
        "<<<<<<< SEARCH\n"
        "    return price + (price * missing_tax)\n"
        "=======\n"
        "    return price + (price * tax)\n"
        ">>>>>>> REPLACE"
    )

    res = tool.execute({"patch_text": patch_text})

    assert res.status == "ERROR"
    assert "SEARCH block 0 error" in res.error
    assert "not found verbatim or normalized" in res.error


def test_apply_patch_schema_requires_verbatim_search_without_ellipsis():
    tool = ApplyPatchTool()
    schema = tool.schema()
    description = schema["function"]["parameters"]["properties"]["patch_text"]["description"]

    assert "must exactly quote complete lines" in description
    assert "..." not in description

    err_res = tool.execute({"patch_text": "invalid"})
    assert err_res.status == "ERROR"
    assert "..." not in err_res.error



def test_inspect_failure_tool():
    tool = InspectFailureTool()
    raw = (
        "FAILED tests/test_calc.py::test_compute_total - AssertionError: assert 100 == 110\n"
        "Traceback (most recent call last):\n"
        "  File 'pkg/test_calc.py', line 3, in test_compute_total\n"
        "    assert compute_total(100, 0.1) == 110\n"
        "AssertionError: assert 100 == 110\n"
    )
    res = tool.execute({"raw_output": raw})
    assert res.status == "SUCCESS"
    assert len(res.data["failing_tests"]) == 1
    assert "AssertionError" in res.data["traceback_snippet"]


def test_git_diff_tool():
    tool = GitDiffTool()
    from patchforge.agent.state import AgentState
    from patchforge.issue.problem import Problem
    from patchforge.repair.patch import Patch
    prob = Problem("test-1", "issue", "repo", "abc")
    state = AgentState("test-1", prob)
    state.active_patch = Patch(patch_text="--- a/foo.py\n+++ b/foo.py\n@@ -1 +1 @@\n-1\n+2\n")
    res = tool.execute({}, state=state)
    assert res.status == "SUCCESS"
    assert "--- a/foo.py" in res.data["diff"]


def test_run_reproduction_tool(tmp_path):
    tool = RunReproductionTool()
    from patchforge.agent.state import AgentState
    from patchforge.issue.problem import Problem
    prob = Problem("test-1", "issue", "repo", "abc")
    state = AgentState("test-1", prob, repo_dir=str(tmp_path))
    repro_code = "print('Reproduced bug')\nraise SystemExit(1)\n"
    res = tool.execute({"repro_code": repro_code}, state=state)
    assert res.status == "SUCCESS"
    assert res.data["exit_code"] == 1
    assert res.data["reproduced"] is True


def test_repograph_fallback_mode(tmp_path):
    from patchforge.integrations.repograph import RepoGraphAdapter, GraphMode
    adapter = RepoGraphAdapter()
    ctx = adapter.related_symbols("", "my_func")
    assert ctx.mode == GraphMode.NO_GRAPH_FALLBACK
    assert ctx.related == []

