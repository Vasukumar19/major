"""Unit tests for PatchForge AI V0.4.3:
MultiSiteRepairTarget, Dynamic Defect Scope Expansion & Search-Block Diff-Marker Robustness.
"""
from __future__ import annotations

from pathlib import Path
import pytest

from patchforge.core.target import EditSite, MultiSiteRepairTarget, RepairTarget
from patchforge.repair.generator import _apply_single_edit, parse_edits
from patchforge.repository.map import RepoMap
from patchforge.retrieval.evidence import ExecutionEvidence
from patchforge.tools.editor import ApplyPatchTool
from patchforge.verification.analyzer import FailureAnalyzer


def test_edit_site_and_multisite_repair_target():
    primary = RepairTarget(
        repository="pallets/flask",
        file_path="src/flask/scaffold.py",
        symbol="_endpoint_from_view_func",
        line_start=130,
        line_end=150,
        source_span=(130, 150),
        verified_source="def _endpoint_from_view_func(view_func):\n    return view_func.__name__\n",
        verification_status=True,
    )

    assert not primary.has_secondary_sites()
    assert len(primary.all_sites()) == 1
    assert primary.primary_site.symbol == "_endpoint_from_view_func"
    assert primary.check_file_match("src/flask/scaffold.py")
    assert not primary.check_file_match("src/flask/blueprints.py")

    sec = EditSite(
        file_path="src/flask/blueprints.py",
        symbol="add_url_rule",
        line_start=350,
        line_end=370,
        verified_source="    def add_url_rule(self, rule, endpoint=None):\n        pass\n",
    )

    added = primary.add_secondary_site(sec)
    assert added is True
    assert primary.has_secondary_sites()
    assert len(primary.all_sites()) == 2
    assert primary.check_file_match("src/flask/blueprints.py")

    # Duplicate should be rejected
    added_dup = primary.add_secondary_site(sec)
    assert added_dup is False
    assert len(primary.all_sites()) == 2

    # Serialization roundtrip
    d = primary.to_dict()
    assert len(d["secondary_sites"]) == 1
    assert d["secondary_sites"][0]["file_path"] == "src/flask/blueprints.py"

    restored = RepairTarget.from_dict(d)
    assert restored.has_secondary_sites()
    assert len(restored.all_sites()) == 2
    assert restored.secondary_sites[0].symbol == "add_url_rule"


def test_search_block_diff_marker_sanitization():
    clean_source = "def example(val):\n    result = val * 2\n    return result\n"

    # Search block with accidental diff '+' markers
    dirty_search = "+    result = val * 2\n+    return result"
    replace_block = "    result = val * 3\n    return result"

    # Should sanitize leading '+' and apply successfully
    res, tier = _apply_single_edit(clean_source, dirty_search, replace_block)
    assert "result = val * 3" in res
    assert tier == "EXACT"

    # Search block with mixed diff '-' markers and whitespace
    dirty_search_2 = "-    result = val * 2\n-    return result"
    replace_block_2 = "    result = val * 4\n    return result"
    res2, tier2 = _apply_single_edit(clean_source, dirty_search_2, replace_block_2)
    assert "result = val * 4" in res2


def test_failure_analyzer_secondary_site_extraction(tmp_path: Path):
    # Create mock repo with two files
    f1 = tmp_path / "scaffold.py"
    f1.write_text(
        "def _check_name(name):\n"
        "    if '.' in name:\n"
        "        raise ValueError('name cannot contain dot')\n",
        encoding="utf-8",
    )

    f2 = tmp_path / "blueprints.py"
    f2.write_text(
        "class Blueprint:\n"
        "    def __init__(self, name):\n"
        "        self.name = name\n"
        "\n"
        "    def add_url_rule(self, rule, endpoint=None):\n"
        "        if endpoint:\n"
        "            assert '.' not in endpoint, 'no dots'\n"
        "        return rule\n",
        encoding="utf-8",
    )

    repo_map = RepoMap(str(tmp_path))
    target = RepairTarget(
        repository="mock/repo",
        file_path="scaffold.py",
        symbol="_check_name",
        line_start=1,
        line_end=3,
        source_span=(1, 3),
        verified_source=f1.read_text(),
        verification_status=True,
    )

    mock_traceback = """
Traceback (most recent call last):
  File "test_blueprints.py", line 45, in test_endpoint
    bp.add_url_rule('/route', 'custom.endpoint')
  File "blueprints.py", line 7, in add_url_rule
    assert '.' not in endpoint, 'no dots'
AssertionError: no dots
"""
    evidence = ExecutionEvidence(
        task_id="test-task",
        target_tests_total=2,
        target_tests_passed=1,
        target_tests_failed=1,
        regression_tests_total=10,
        regression_tests_passed=10,
        regression_tests_failed=0,
        traceback=mock_traceback,
    )

    analyzer = FailureAnalyzer()
    new_sites = analyzer.extract_secondary_edit_sites(evidence, repo_map, target)

    assert len(new_sites) == 1
    assert new_sites[0].file_path == "blueprints.py"
    assert new_sites[0].symbol == "add_url_rule"
    assert "assert '.' not in endpoint" in new_sites[0].verified_source


def test_apply_patch_tool_allowed_files(tmp_path: Path):
    f1 = tmp_path / "app.py"
    f1.write_text("def hello():\n    return 'hello'\n", encoding="utf-8")
    f2 = tmp_path / "secret.py"
    f2.write_text("SECRET_KEY = '12345'\n", encoding="utf-8")

    tool = ApplyPatchTool(repo_dir=str(tmp_path))

    # Attempt to modify secret.py when only app.py is allowed
    disallowed_patch = (
        "### secret.py\n"
        "<<<<<<< SEARCH\n"
        "SECRET_KEY = '12345'\n"
        "=======\n"
        "SECRET_KEY = 'hacked'\n"
        ">>>>>>> REPLACE\n"
    )

    res = tool.execute({
        "patch_text": disallowed_patch,
        "allowed_files": ["app.py"],
    })
    assert res.status == "ERROR"
    assert "TARGET_MISMATCH" in res.error

    # Attempt to modify app.py when app.py is allowed
    allowed_patch = (
        "### app.py\n"
        "<<<<<<< SEARCH\n"
        "    return 'hello'\n"
        "=======\n"
        "    return 'world'\n"
        ">>>>>>> REPLACE\n"
    )

    res2 = tool.execute({
        "patch_text": allowed_patch,
        "allowed_files": ["app.py"],
    })
    assert res2.status == "SUCCESS"
    assert "world" in (tmp_path / "app.py").read_text()
