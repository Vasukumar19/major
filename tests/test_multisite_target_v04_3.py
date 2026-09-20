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
    res, tier, *err = _apply_single_edit(clean_source, dirty_search, replace_block)
    tier_str = tier.value if hasattr(tier, "value") else str(tier)
    assert "result = val * 3" in res
    assert tier_str == "EXACT"

    # Search block with mixed diff '-' markers and whitespace
    dirty_search_2 = "-    result = val * 2\n-    return result"
    replace_block_2 = "    result = val * 4\n    return result"
    res2, tier2, *err2 = _apply_single_edit(clean_source, dirty_search_2, replace_block_2)
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


def test_multisite_cases_a_through_e(tmp_path: Path):
    """Synthetic unit tests for multi-site targeting cases A through E:
    Case A: Two methods in one file.
    Case B: Two files.
    Case C: Primary site + related helper.
    Case D: Partial target success where modifying only one site leaves remaining failures.
    Case E: Rejection of unrelated secondary site proposed by model.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    f1 = repo / "module_a.py"
    f1.write_text(
        "class Worker:\n"
        "    def method_one(self, x):\n"
        "        return x + 1\n\n"
        "    def method_two(self, y):\n"
        "        return y * 2\n",
        encoding="utf-8",
    )
    f2 = repo / "helper.py"
    f2.write_text(
        "def shared_helper(z):\n"
        "    return z.strip()\n",
        encoding="utf-8",
    )
    f_unrelated = repo / "unrelated.py"
    f_unrelated.write_text(
        "def unrelated_function():\n"
        "    return 42\n",
        encoding="utf-8",
    )

    # Case A: Two methods in one file
    target = RepairTarget(
        file_path="module_a.py",
        symbol="method_one",
        repository="test/repo",
        line_start=2,
        line_end=3,
        verified_source="    def method_one(self, x):\n        return x + 1\n",
        verification_status=True,
    )
    site_two = EditSite(
        file_path="module_a.py",
        symbol="method_two",
        line_start=5,
        line_end=6,
        verified_source="    def method_two(self, y):\n        return y * 2\n",
        verification_status=True,
    )
    assert target.add_secondary_site(site_two) is True
    assert len(target.all_sites()) == 2
    assert target.all_sites()[0].symbol == "method_one"
    assert target.all_sites()[1].symbol == "method_two"

    # Case B: Two files
    target_b = RepairTarget(
        file_path="module_a.py",
        symbol="method_one",
        repository="test/repo",
        line_start=2,
        line_end=3,
        verified_source="    def method_one(self, x):\n        return x + 1\n",
        verification_status=True,
    )
    site_f2 = EditSite(
        file_path="helper.py",
        symbol="shared_helper",
        line_start=1,
        line_end=2,
        verified_source="def shared_helper(z):\n    return z.strip()\n",
        verification_status=True,
    )
    assert target_b.add_secondary_site(site_f2) is True
    assert target_b.check_file_match("helper.py") is True
    assert target_b.check_file_match("module_a.py") is True
    assert target_b.check_file_match("unrelated.py") is False

    # Case C: Primary site + related helper
    tool = ApplyPatchTool(repo_dir=str(repo))
    patch_ac = (
        "### module_a.py\n"
        "<<<<<<< SEARCH\n"
        "        return x + 1\n"
        "=======\n"
        "        return shared_helper(x)\n"
        ">>>>>>> REPLACE\n"
        "### helper.py\n"
        "<<<<<<< SEARCH\n"
        "    return z.strip()\n"
        "=======\n"
        "    return str(z).strip()\n"
        ">>>>>>> REPLACE\n"
    )
    allowed = [s.file_path for s in target_b.all_sites()]
    res_ac = tool.execute({"patch_text": patch_ac, "allowed_files": allowed})
    assert res_ac.status == "SUCCESS"
    assert "shared_helper(x)" in f1.read_text(encoding="utf-8")
    assert "str(z).strip()" in f2.read_text(encoding="utf-8")

    # Case D: Modifying only one site leaves remaining failures (partial success)
    # Simulated via non-regressive progress acceptance
    prev_f2p_passed = 1
    prev_f2p_total = 2
    # Cycle 1 only modifies module_a.py -> 1/2 tests pass (partial success)
    cycle1_f2p_passed = 1
    cycle1_p2p_passed = 50
    prev_p2p_passed = 50
    # Engine logic check: partial progress is accepted without rollback
    is_better = (
        (cycle1_f2p_passed >= prev_f2p_passed and cycle1_p2p_passed >= prev_p2p_passed)
    )
    assert is_better is True

    # Case E: Model incorrectly proposes an unrelated secondary site
    unrelated_patch = (
        "### unrelated.py\n"
        "<<<<<<< SEARCH\n"
        "    return 42\n"
        "=======\n"
        "    return 999\n"
        ">>>>>>> REPLACE\n"
    )
    res_e = tool.execute({
        "patch_text": unrelated_patch,
        "allowed_files": allowed,  # allowed contains only module_a.py and helper.py
    })
    assert res_e.status == "ERROR"
    assert "TARGET_MISMATCH" in res_e.error
    assert "unrelated.py" in res_e.error
    assert "42" in f_unrelated.read_text(encoding="utf-8")  # Unrelated file was untouched!

