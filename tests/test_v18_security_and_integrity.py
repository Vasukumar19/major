"""Regression tests for V1.8: Security, Evaluation Integrity, Transactional Patches, and Cache Isolation."""
import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from patchforge.integrations.swebench import SWEBenchAdapter, validate_evaluation_consistency
from patchforge.repair.reconstructor import SourceReconstructor
from patchforge.repository_intelligence.indexer import RepositoryIndexer, _get_git_commit
from patchforge.tools.editor import ApplyPatchTool, ReadFileTool, ReadTestTool


def test_read_file_tool_blocks_path_traversal(tmp_path):
    repo_dir = tmp_path / "workspace"
    repo_dir.mkdir()
    secret_file = tmp_path / "secret.txt"
    secret_file.write_text("SUPER_SECRET_TOKEN", encoding="utf-8")

    tool = ReadFileTool(repo_dir=str(repo_dir))
    res1 = tool.execute({"file_path": "../secret.txt"})
    assert res1.status == "ERROR"
    assert "PATH_SECURITY_ERROR" in res1.error

    res2 = tool.execute({"file_path": str(secret_file.resolve())})
    assert res2.status == "ERROR"
    assert "PATH_SECURITY_ERROR" in res2.error

    test_tool = ReadTestTool(repo_dir=str(repo_dir))
    res3 = test_tool.execute({"test_file": "../secret.txt"})
    assert res3.status == "ERROR"
    assert "PATH_SECURITY_ERROR" in res3.error


def test_apply_patch_tool_blocks_traversal_and_escapes(tmp_path):
    repo_dir = tmp_path / "workspace"
    repo_dir.mkdir()
    secret_file = tmp_path / "secret.txt"
    secret_file.write_text("ORIGINAL", encoding="utf-8")

    patch_text = "### ../secret.txt\n<<<<<<< SEARCH\nORIGINAL\n=======\nHACKED\n>>>>>>> REPLACE"
    tool = ApplyPatchTool(repo_dir=str(repo_dir))
    res = tool.execute({"patch_text": patch_text})

    assert res.status == "ERROR"
    assert "PATH_SECURITY_ERROR" in res.error
    assert secret_file.read_text(encoding="utf-8") == "ORIGINAL"


def test_apply_patch_transactional_rollback_on_failure(tmp_path):
    repo_dir = tmp_path / "workspace"
    repo_dir.mkdir()
    f1 = repo_dir / "module_a.py"
    f2 = repo_dir / "module_b.py"
    f1.write_text("x = 1\n", encoding="utf-8")
    f2.write_text("y = 1\n", encoding="utf-8")

    # file 1 has a valid patch, but file 2 has a SEARCH mismatch
    patch_text = (
        "### module_a.py\n<<<<<<< SEARCH\nx = 1\n=======\nx = 2\n>>>>>>> REPLACE\n"
        "### module_b.py\n<<<<<<< SEARCH\nNON_EXISTENT_CONTENT\n=======\ny = 2\n>>>>>>> REPLACE"
    )

    tool = ApplyPatchTool(repo_dir=str(repo_dir))
    res = tool.execute({"patch_text": patch_text})

    assert res.status == "ERROR"
    # Neither file should be modified on disk! Atomic validate-before-write guarantee
    assert f1.read_text(encoding="utf-8") == "x = 1\n"
    assert f2.read_text(encoding="utf-8") == "y = 1\n"


def test_apply_patch_transactional_rollback_on_syntax_error(tmp_path):
    repo_dir = tmp_path / "workspace"
    repo_dir.mkdir()
    f1 = repo_dir / "valid.py"
    f2 = repo_dir / "broken.py"
    f1.write_text("def a(): pass\n", encoding="utf-8")
    f2.write_text("def b(): pass\n", encoding="utf-8")

    # file 1 is valid, but file 2 introduces a syntax error
    patch_text = (
        "### valid.py\n<<<<<<< SEARCH\ndef a(): pass\n=======\ndef a(): return 1\n>>>>>>> REPLACE\n"
        "### broken.py\n<<<<<<< SEARCH\ndef b(): pass\n=======\ndef b(: pass\n>>>>>>> REPLACE"
    )

    tool = ApplyPatchTool(repo_dir=str(repo_dir))
    res = tool.execute({"patch_text": patch_text})

    assert res.status == "ERROR"
    assert "Syntax validation failed on broken.py" in res.error
    # Neither file should be modified
    assert f1.read_text(encoding="utf-8") == "def a(): pass\n"
    assert f2.read_text(encoding="utf-8") == "def b(): pass\n"


def test_reconstructor_builtin_names_not_imported():
    code = "def f():\n    val = str(123)\n    raw = bytes([1, 2])\n    num = int('42')\n    return val\n"
    healed = SourceReconstructor._auto_heal_missing_imports(code, "test.py")
    assert "import str" not in healed
    assert "import bytes" not in healed
    assert "import int" not in healed
    assert "_pytest.compat" not in healed


def test_swebench_adapter_strictly_run_scoped(tmp_path):
    ad = SWEBenchAdapter()

    # Outside report in root or wrong run_id must NOT be picked up
    wrong_run_dir = Path("logs") / "run_evaluation" / "different_run_id" / "eval_model" / "psf__requests-863"
    wrong_run_dir.mkdir(parents=True, exist_ok=True)
    (wrong_run_dir / "report.json").write_text(json.dumps({"psf__requests-863": {"resolved": True}}), encoding="utf-8")

    try:
        rep = ad._find_report("my_isolated_run_id", "psf__requests-863")
        assert rep is None
    finally:
        import shutil
        shutil.rmtree(Path("logs") / "run_evaluation" / "different_run_id", ignore_errors=True)


def test_validate_evaluation_consistency():
    from patchforge.integrations.swebench import EvalResult
    ev = EvalResult(
        instance_id="task_1",
        resolved=True,
        fail_to_pass_success=["tests/test_foo.py::test_a"],
        pass_to_pass_success=["tests/test_foo.py::test_b"],
    )
    test_patch = "def test_a(): pass\ndef test_b(): pass\n"
    res = validate_evaluation_consistency(ev, test_patch=test_patch, raw_test_output="PASSED test_a\nPASSED test_b")
    assert res["verified"] is True
    assert res["inconsistent"] is False

    # When test output contains failure of a test not declared in failures
    res_bad = validate_evaluation_consistency(ev, test_patch="def test_untracked(): pass\n", raw_test_output="FAILED test_untracked")
    assert "test_untracked" in res_bad["unrepresented_failures"]
    assert res_bad["inconsistent"] is True


def test_indexer_dirty_cache_invalidation(tmp_path):
    repo_p = tmp_path / "mock_repo"
    repo_p.mkdir()
    subprocess.run(["git", "init"], cwd=str(repo_p), capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=str(repo_p), capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(repo_p), capture_output=True)

    f = repo_p / "main.py"
    f.write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(repo_p), capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo_p), capture_output=True)

    clean_state = _get_git_commit(str(repo_p))

    # Dirty unstaged change
    f.write_text("x = 2\n", encoding="utf-8")
    dirty_state = _get_git_commit(str(repo_p))
    assert clean_state != dirty_state

    # Add untracked file
    f.write_text("x = 1\n", encoding="utf-8")  # restore main.py
    untracked = repo_p / "untracked.py"
    untracked.write_text("z = 99\n", encoding="utf-8")
    untracked_state = _get_git_commit(str(repo_p))
    assert clean_state != untracked_state
