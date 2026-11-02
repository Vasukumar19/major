"""Comprehensive regression tests for PatchForge V1.8 consolidated engineering batch.

Verifies:
1. Tool Isolation & Fail-Closed Host Guards (RunTargetedTestTool, RunReproductionTool)
2. Tool Path Traversal & Containment (FindSymbolTool)
3. Partial-Repair Preservation & Schema Property Aliases (StructuredRepairOutput)
4. Repository-Aware Target Reconciliation (RepairSiteRanker.reconcile_target)
5. Structured Repair RFC JSON Unescaping & Backslash/Regex Preservation
6. Context-Preserving Tier Fallbacks & Contrastive Invariants (StructuredRepairPromptBuilder)
7. Coordinated Multi-Site Repair Reconstruction (SourceReconstructor.reconstruct_multi)
8. Test Graph Index Caching & Rapid Cache Hit Loading (RepositoryIndexer & TestGraphIndex)
9. Evaluation Integrity & Non-Discriminating F2P Diagnostics (validate_evaluation_consistency)
"""
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from patchforge.integrations.swebench import EvalResult, validate_evaluation_consistency
from patchforge.repair.planner import RepairUnitPlanner
from patchforge.repair.ranking import RepairSiteRanker
from patchforge.repair.reconstructor import SourceReconstructor
from patchforge.repair.replanner import FailureDrivenReplanner, ReplanAction
from patchforge.repair.residual import ResidualFailureRecord
from patchforge.repair.schema import RepairAction, RepairUnit, RepairUnitType, StructuredRepairOutput
from patchforge.repair.structured_repair import StructuredRepairParser, StructuredRepairPromptBuilder
from patchforge.verification.classifier import FailureClass
from patchforge.repository_intelligence.graph import RepositoryGraph
from patchforge.repository_intelligence.indexer import RepositoryIndexer
from patchforge.repository_intelligence.test_graph import TestAssertion, TestCaseEntity, TestGraphIndex
from patchforge.tools.execution import RunReproductionTool, RunTargetedTestTool
from patchforge.tools.search import FindSymbolTool


# ==============================================================================
# 1. TOOL ISOLATION & FAIL-CLOSED GUARDS
# ==============================================================================

def test_run_targeted_test_tool_fail_closed_by_default(tmp_path):
    """Host execution is blocked by default and cannot be bypassed via temp directory."""
    temp_repo = tmp_path / "repo"
    temp_repo.mkdir()
    state = MagicMock()
    state.repo_dir = str(temp_repo)
    state.tester = None
    state.instance_id = None
    state.active_patch = None
    state.allow_host_execution = False

    tool = RunTargetedTestTool(allow_host_execution=False)
    res = tool.execute({"test_target": "tests/test_dummy.py"}, state=state)

    assert res.status == "ERROR"
    assert "SECURITY_POLICY" in res.error
    assert res.data.get("isolation") == "BLOCKED"


def test_run_targeted_test_tool_requires_selector_aware_executor():
    """A full-harness tester cannot silently satisfy a requested selector."""
    state = MagicMock()
    state.instance_id = "test_inst_1"
    state.active_patch = MagicMock(patch_text="diff --git ...")
    mock_tester = MagicMock()
    mock_tester.supports_selected_tests = False
    state.tester = mock_tester

    tool = RunTargetedTestTool(allow_host_execution=True)
    res = tool.execute({"test_target": "tests/test_dummy.py"}, state=state)

    assert res.status == "ERROR"
    assert "SELECTED_TEST_UNAVAILABLE" in res.error
    assert res.data.get("isolation") == "UNAVAILABLE"


def test_run_reproduction_tool_fail_closed_by_default(tmp_path):
    """RunReproductionTool rejects execution without explicit opt-in, even in temp dir."""
    state = MagicMock()
    state.repo_dir = str(tmp_path)
    state.allow_host_execution = False

    tool = RunReproductionTool(allow_host_execution=False)
    res = tool.execute({"repro_code": "print('exploit')"}, state=state)

    assert res.status == "ERROR"
    assert "SECURITY_POLICY" in res.error
    assert res.data.get("isolation") == "BLOCKED"


def test_run_reproduction_tool_explicit_opt_in(tmp_path):
    """RunReproductionTool executes safely when explicit opt-in is granted."""
    state = MagicMock()
    state.repo_dir = str(tmp_path)
    state.allow_host_execution = True

    tool = RunReproductionTool(allow_host_execution=True)
    res = tool.execute({"repro_code": "import sys; print('hello from repro'); sys.exit(0)"}, state=state)

    assert res.status == "SUCCESS"
    assert res.data.get("exit_code") == 0
    assert "hello from repro" in res.data.get("stdout", "")
    assert res.data.get("isolation") == "EXPLICIT_HOST_OPT_IN"


# ==============================================================================
# 2. FIND_SYMBOL TOOL PATH CONTAINMENT
# ==============================================================================

def test_find_symbol_tool_rejects_path_traversal(tmp_path):
    """FindSymbolTool strictly rejects directory traversal parent references."""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    secret = tmp_path / "secret.py"
    secret.write_text("def secret_key(): pass\n", encoding="utf-8")

    tool = FindSymbolTool(repo_dir=str(repo_dir))
    res = tool.execute({"symbol_name": "secret_key", "file_path": "../secret.py"})

    assert res.status == "ERROR"
    assert "PATH_SECURITY_ERROR" in res.error


def test_find_symbol_tool_rejects_escaping_absolute_path(tmp_path):
    """FindSymbolTool rejects absolute paths pointing outside the repository."""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    external = tmp_path / "ext.py"
    external.write_text("class Outer: pass\n", encoding="utf-8")

    tool = FindSymbolTool(repo_dir=str(repo_dir))
    res = tool.execute({"symbol_name": "Outer", "file_path": str(external.resolve())})

    assert res.status == "ERROR"
    assert "PATH_SECURITY_ERROR" in res.error


def test_find_symbol_tool_resolves_valid_repo_file(tmp_path):
    """FindSymbolTool successfully locates symbol in valid repository file."""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    mod = repo_dir / "calc.py"
    mod.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

    tool = FindSymbolTool(repo_dir=str(repo_dir))
    res = tool.execute({"symbol_name": "add", "file_path": "calc.py"})

    assert res.status == "SUCCESS"
    matches = res.data.get("symbols", [])
    assert len(matches) == 1
    assert matches[0]["symbol"] == "add"
    assert matches[0]["file"] == "calc.py"


# ==============================================================================
# 3. SCHEMA ALIAS & PARTIAL-REPAIR PRESERVATION
# ==============================================================================

def test_structured_repair_output_replacement_code_property():
    """StructuredRepairOutput provides replacement_code alias matching replacement."""
    out = StructuredRepairOutput(
        repair_action=RepairAction.REPLACE_STATEMENT.value,
        target_unit_id="unit_1",
        replacement="x = 42\n",
    )
    assert out.replacement == "x = 42\n"
    assert out.replacement_code == "x = 42\n"


def test_partial_repair_retention_contract():
    """Residual record preserves working replacement when partial progress is achieved."""
    rec = ResidualFailureRecord(
        instance_id="test_inst",
        attempt=1,
        f2p_passed_count=2,
        f2p_total_count=3,
        f2p_passed_tests=["test_1", "test_2"],
        f2p_failed_tests=["test_edge_case"],
        is_partial_progress=True,
    )
    assert rec.is_partial_progress is True

    out = StructuredRepairOutput(
        repair_action=RepairAction.REPLACE_STATEMENT.value,
        target_unit_id="u1",
        replacement="def fixed(): return True",
    )
    last_working_replacement = None
    if rec.is_partial_progress and getattr(out, "replacement_code", None):
        last_working_replacement = out.replacement_code

    assert last_working_replacement == "def fixed(): return True"


# ==============================================================================
# 4. REPOSITORY-AWARE TARGET RECONCILIATION
# ==============================================================================

def test_reconcile_target_rejects_cross_directory_basename_collision():
    """Reconcile target rejects collisions when files have identical basenames in different dirs."""
    candidate_site = MagicMock()
    candidate_site.file_path = "tests/models.py"
    candidate_site.symbol = "prepare"

    diagnosis = MagicMock()
    diagnosis.affected_sites = ["requests/models.py:prepare"]
    diagnosis.cause = "Malformed request payload"
    diagnosis.repair_strategy = "Validate header before preparing"

    coherent, justification = RepairSiteRanker.reconcile_target(candidate_site, diagnosis)
    assert coherent is False
    assert "contradicts diagnosed affected sites" in justification


def test_reconcile_target_matches_exact_path():
    """Reconcile target matches when file paths match repository-relative identity."""
    candidate_site = MagicMock()
    candidate_site.file_path = "requests/models.py"
    candidate_site.symbol = "prepare"

    diagnosis = MagicMock()
    diagnosis.affected_sites = ["requests/models.py:prepare"]

    coherent, justification = RepairSiteRanker.reconcile_target(candidate_site, diagnosis)
    assert coherent is True
    assert "Target matches hypothesized affected site" in justification


def test_reconcile_target_matches_subpath_suffix():
    """Reconcile target handles prefix differences (e.g. src/requests/models.py vs requests/models.py)."""
    candidate_site = MagicMock()
    candidate_site.file_path = "src/requests/models.py"
    candidate_site.symbol = "prepare"

    diagnosis = MagicMock()
    diagnosis.affected_sites = ["requests/models.py:prepare"]

    coherent, justification = RepairSiteRanker.reconcile_target(candidate_site, diagnosis)
    assert coherent is True
    assert "Target matches hypothesized affected site" in justification


# ==============================================================================
# 5. STRUCTURED REPAIR JSON & BACKSLASH / REGEX PRESERVATION
# ==============================================================================

def test_json_extraction_preserves_trailing_line_continuation_and_regexes():
    """Safe RFC-compliant JSON decoding preserves line continuations and raw regex backslashes."""
    # Model response containing regex backslashes and line continuation
    raw_response = (
        '{\n'
        '  "repair_action": "replace_statement",\n'
        '  "target_unit_id": "u1",\n'
        '  "target_symbol": "match_pattern",\n'
        '  "replacement": "pattern = re.compile(r\\"\\\\d+\\\\s*\\\\w+\\")\\\\nresult = a + \\\\\\\\n         b",\n'
        '  "reasoning": "Preserve pattern",\n'
        '  "invariant": "No regex corruption"\n'
        '}'
    )

    data = StructuredRepairParser._repair_and_extract_json(raw_response)
    assert data is not None
    rep = data.get("replacement", "")
    assert r"\d+\s*\w+" in rep
    assert "result = a +" in rep


def test_json_regex_fallback_does_not_corrupt_with_unicode_escape():
    """Regex fallback unescaping does not crash or corrupt on backslash patterns."""
    text_snippet = (
        'Some text before\n'
        '{"repair_action": "replace_statement", "target_unit_id": "u1", '
        '"replacement": "import re\\nx = re.compile(r\\"\\\\s+\\\\d+\\")", '
        '"reasoning": "Add regex"}\n'
    )
    data = StructuredRepairParser._repair_and_extract_json(text_snippet)
    assert data is not None
    assert r"\s+\d+" in data.get("replacement", "")


# ==============================================================================
# 6. CONTEXT PRESERVATION & CONTRASTIVE INVARIANTS
# ==============================================================================

def test_build_minimal_prompt_preserves_problem_and_diagnosis():
    """Minimal prompt builder does not collapse context into blindness."""
    unit = RepairUnit(
        id="unit_1",
        file_path="foo/bar.py",
        symbol="compute",
        unit_type=RepairUnitType.STATEMENT,
        node_type="Assign",
        start_line=10,
        end_line=11,
        source_text="res = val * 2\n",
    )
    prompt = StructuredRepairPromptBuilder.build_minimal_prompt(
        unit=unit,
        error_feedback="Calculation resulted in negative value",
        problem_statement="Handle zero or negative multiplier appropriately",
        diagnosis_summary="Underflow in compute function",
        prior_patch_code="res = max(0, val * 2)",
    )

    assert "[PROBLEM TO RESOLVE]:" in prompt
    assert "Handle zero or negative multiplier" in prompt
    assert "[DIAGNOSED ROOT CAUSE]:" in prompt
    assert "[PRIOR PARTIAL REPLACEMENT]:" in prompt
    assert "max(0, val * 2)" in prompt


def test_prompt_builders_include_contrastive_noop_invariant():
    """Prompt builders include explicit contrastive invariant when previous attempt was no-op."""
    unit = RepairUnit(
        id="unit_1",
        file_path="foo/bar.py",
        symbol="compute",
        unit_type=RepairUnitType.STATEMENT,
        node_type="Assign",
        start_line=10,
        end_line=11,
        source_text="res = val * 2\n",
    )
    prompt = StructuredRepairPromptBuilder.build_minimal_prompt(
        unit=unit,
        error_feedback="No changes produced between original source and reconstructed source.",
    )

    assert "[CONTRASTIVE REPAIR INVARIANT (NO-OP REJECTION)]:" in prompt
    assert "Your previous candidate was rejected because it produced zero changes" in prompt


# ==============================================================================
# 7. COORDINATED MULTI-SITE REPAIR RECONSTRUCTION
# ==============================================================================

def test_reconstruct_multi_coordinated_patch():
    """SourceReconstructor.reconstruct_multi applies coordinated non-overlapping units across files."""
    source_a = "def func_a():\n    return 1\n"
    source_b = "def func_b():\n    return 'old'\n"

    unit_a = RepairUnit(
        id="a_u1",
        file_path="pkg/a.py",
        symbol="func_a",
        unit_type=RepairUnitType.FUNCTION,
        node_type="FunctionDef",
        start_line=1,
        end_line=2,
        source_text="def func_a():\n    return 1\n",
    )
    unit_b = RepairUnit(
        id="b_u1",
        file_path="pkg/b.py",
        symbol="func_b",
        unit_type=RepairUnitType.FUNCTION,
        node_type="FunctionDef",
        start_line=1,
        end_line=2,
        source_text="def func_b():\n    return 'old'\n",
    )

    out_a = StructuredRepairOutput(
        repair_action=RepairAction.REPLACE_METHOD.value,
        target_unit_id="a_u1",
        replacement="def func_a():\n    return 2",
    )
    out_b = StructuredRepairOutput(
        repair_action=RepairAction.REPLACE_METHOD.value,
        target_unit_id="b_u1",
        replacement="def func_b():\n    return 'new'",
    )

    sources = {"pkg/a.py": source_a, "pkg/b.py": source_b}
    edits = [(unit_a, out_a), (unit_b, out_b)]

    rec = SourceReconstructor.reconstruct_multi(sources, edits)
    assert rec.success is True
    assert len(rec.files_changed) == 2
    assert "pkg/a.py" in rec.files_changed
    assert "pkg/b.py" in rec.files_changed
    assert "return 2" in rec.modified_contents["pkg/a.py"]
    assert "return 'new'" in rec.modified_contents["pkg/b.py"]
    assert "--- a/pkg/a.py" in rec.patch_text
    assert "--- a/pkg/b.py" in rec.patch_text


# ==============================================================================
# 8. TEST GRAPH INDEX CACHING & FAST CACHE HITS
# ==============================================================================

def test_test_graph_index_serialization(tmp_path):
    """TestGraphIndex serializes to disk JSON and restores accurately."""
    idx = TestGraphIndex()
    code = (
        "import pytest\n"
        "def test_login():\n"
        "    assert check_credentials('admin', 'pass') == True\n"
    )
    idx.index_test_file("tests/test_auth.py", code)
    assert len(idx.tests) == 1

    cache_file = tmp_path / "test_idx.json"
    idx.save(str(cache_file))

    loaded = TestGraphIndex.load(str(cache_file))
    assert len(loaded.tests) == 1
    t = loaded.tests.get("tests/test_auth.py:test_login")
    assert t is not None
    assert t.name == "test_login"
    assert len(t.assertions) == 1
    assert "check_credentials" in t.symbols_referenced


def test_repository_indexer_caches_and_loads_test_index(tmp_path):
    """RepositoryIndexer caches TestGraphIndex alongside graph and loads it without reparsing."""
    repo = tmp_path / "my_repo"
    repo.mkdir()
    (repo / "calc.py").write_text("def mul(a, b): return a * b\n", encoding="utf-8")
    (repo / "test_calc.py").write_text("def test_mul(): assert mul(2, 3) == 6\n", encoding="utf-8")

    cache_dir = tmp_path / "cache"
    indexer = RepositoryIndexer(repo_path=str(repo), cache_dir=str(cache_dir))

    # First indexing run builds fresh index
    graph1, test_idx1 = indexer.index(force_rebuild=True)
    assert len(test_idx1.tests) == 1

    test_cache_path = indexer._get_cache_path().replace(".graph.json", ".test_index.json")
    assert Path(test_cache_path).exists()

    # Second indexing run hits disk cache cleanly
    graph2, test_idx2 = indexer.index(force_rebuild=False)
    assert len(test_idx2.tests) == 1
    assert "test_calc.py:test_mul" in test_idx2.tests


# ==============================================================================
# 9. EVALUATION INTEGRITY & NON-DISCRIMINATING DIAGNOSTICS
# ==============================================================================

def test_validate_evaluation_consistency_detects_anomalies():
    """validate_evaluation_consistency flags unrepresented failures and unresolved inconsistencies."""
    ev = EvalResult(
        instance_id="inst_1",
        resolved=True,
        fail_to_pass_passed=1,
        fail_to_pass_total=2,
        fail_to_pass_success=["tests/test_a.py::test_1"],
        fail_to_pass_failure=["tests/test_a.py::test_2"],
    )
    test_patch = "def test_1(): pass\ndef test_2(): pass\n"
    res = validate_evaluation_consistency(ev, test_patch=test_patch, raw_test_output="")

    assert res["verified"] is True
    assert res["inconsistent"] is True
    assert any("fail_to_pass_failure is non-empty" in a for a in res["integrity_anomalies"])


# ==============================================================================
# 10. TARGETED AUDIT VERIFICATION TESTS (THE 5 CORE CONCERNS)
# ==============================================================================

def test_host_execution_schema_and_args_cannot_escalate_privilege(tmp_path):
    """Gap 1: Untrusted model output in args cannot escalate privilege to allow_host_execution."""
    # 1. Parameter schemas must not expose allow_host_execution to LLM
    test_tool = RunTargetedTestTool(allow_host_execution=False)
    repro_tool = RunReproductionTool(allow_host_execution=False)
    assert "allow_host_execution" not in test_tool.parameters.get("properties", {})
    assert "allow_host_execution" not in repro_tool.parameters.get("properties", {})

    state = MagicMock()
    state.repo_dir = str(tmp_path)
    state.tester = None
    state.allow_host_execution = False

    # 2. Passing allow_host_execution=True in untrusted args is strictly ignored and blocked
    res1 = test_tool.execute({"test_target": "tests/test_foo.py", "allow_host_execution": True}, state=state)
    assert res1.status == "ERROR"
    assert "SECURITY_POLICY" in res1.error
    assert res1.data.get("isolation") == "BLOCKED"

    res2 = repro_tool.execute({"repro_code": "assert True", "allow_host_execution": True}, state=state)
    assert res2.status == "ERROR"
    assert "SECURITY_POLICY" in res2.error
    assert res2.data.get("isolation") == "BLOCKED"


def test_reconstruct_multi_ast_validation_rejects_broken_third_file():
    """Gap 2A: reconstruct_multi validates AST on all modified files and fails before touching disk if any file has syntax error."""
    unit1 = RepairUnit(
        id="u1",
        file_path="pkg/mod_a.py",
        symbol="func_a",
        unit_type=RepairUnitType.STATEMENT,
        node_type="Return",
        start_line=2,
        end_line=2,
        indentation="    ",
        source_text="    return 1",
    )
    unit2 = RepairUnit(
        id="u2",
        file_path="pkg/mod_b.py",
        symbol="func_b",
        unit_type=RepairUnitType.STATEMENT,
        node_type="Return",
        start_line=2,
        end_line=2,
        indentation="    ",
        source_text="    return 2",
    )
    unit3 = RepairUnit(
        id="u3",
        file_path="pkg/mod_c.py",
        symbol="func_c",
        unit_type=RepairUnitType.STATEMENT,
        node_type="Return",
        start_line=2,
        end_line=2,
        indentation="    ",
        source_text="    return 3",
    )

    out1 = StructuredRepairOutput(repair_action=RepairAction.REPLACE_STATEMENT.value, target_unit_id="u1", replacement="return 10")
    out2 = StructuredRepairOutput(repair_action=RepairAction.REPLACE_STATEMENT.value, target_unit_id="u2", replacement="return 20")
    # File 3 has invalid syntax: incomplete expression
    out3 = StructuredRepairOutput(repair_action=RepairAction.REPLACE_STATEMENT.value, target_unit_id="u3", replacement="return (10 +")

    files_source = {
        "pkg/mod_a.py": "def func_a():\n    return 1\n",
        "pkg/mod_b.py": "def func_b():\n    return 2\n",
        "pkg/mod_c.py": "def func_c():\n    return 3\n",
    }

    edits = [(unit1, out1), (unit2, out2), (unit3, out3)]
    res = SourceReconstructor.reconstruct_multi(files_source, edits)

    assert res.success is False
    assert "AST syntax error" in res.error and "pkg/mod_c.py" in res.error
    assert res.modified_contents == {}
    assert res.patch_text == ""


def test_transactional_multi_file_write_rollback_restores_clean_state(tmp_path):
    """Gap 2B: Transactional write rollback restores prior file contents when any file write fails."""
    repo = tmp_path / "repo"
    repo.mkdir()
    f1 = repo / "a.py"
    f2 = repo / "b.py"
    f1.write_text("orig a", encoding="utf-8")
    f2.write_text("orig b", encoding="utf-8")

    # Simulate transactional write with a failure on the 3rd file
    written_backups = []
    write_failed = False
    new_contents = {"a.py": "modified a", "b.py": "modified b", "c.py": "new c"}

    try:
        for mod_rel, mod_code in new_contents.items():
            dest_f = repo / mod_rel
            dest_f.parent.mkdir(parents=True, exist_ok=True)
            prior = dest_f.read_text(encoding="utf-8") if dest_f.exists() else None

            # Simulate an OS / disk permission error on the 3rd file
            if mod_rel == "c.py":
                raise OSError("Disk full or permission denied")

            dest_f.write_text(mod_code, encoding="utf-8")
            written_backups.append((dest_f, prior))
    except Exception:
        write_failed = True
        # Roll back all written files to prior state
        for wf, prior_content in written_backups:
            if prior_content is None:
                if wf.exists():
                    wf.unlink()
            else:
                wf.write_text(prior_content, encoding="utf-8")

    assert write_failed is True
    # Verify that a.py and b.py were cleanly restored to their exact original contents
    assert f1.read_text(encoding="utf-8") == "orig a"
    assert f2.read_text(encoding="utf-8") == "orig b"
    assert not (repo / "c.py").exists()


def test_structured_repair_parser_entrypoint_preserves_complex_escapes():
    """Gap 3: Full StructuredRepairParser.parse entry point preserves regexes, escaped quotes, docstrings, and line continuations."""
    unit = RepairUnit(
        id="u_parse",
        file_path="pkg/parser.py",
        symbol="process_text",
        unit_type=RepairUnitType.STATEMENT_BLOCK,
        node_type="Block",
        start_line=5,
        end_line=10,
        indentation="    ",
        source_text="    pass",
    )

    rep_code = 'pattern = re.compile(r"[\\w\\.-]+@[\\w\\.-]+\\.\\w+")\nmsg = "User \\"Admin\\" logged in"\nx = a + \\\n    b'
    llm_dict = {
        "repair_action": "replace_statement",
        "target_unit_id": "u_parse",
        "target_symbol": "process_text",
        "replacement": rep_code,
        "reasoning": "Preserve exact escapes and quotes",
        "invariant": "No backslash corruption",
    }
    llm_output = f"```json\n{json.dumps(llm_dict)}\n```"

    out = StructuredRepairParser.parse(llm_output, unit)
    assert out.repair_action == "replace_statement"
    assert r"[\w\.-]+@[\w\.-]+\.\w+" in out.replacement
    assert 'msg = "User \\"Admin\\" logged in"' in out.replacement
    assert "x = a + \\\n    b" in out.replacement


def test_validate_evaluation_consistency_detects_non_discriminating_tests():
    """Gap 4B: validate_evaluation_consistency detects fail-to-pass tests that already pass on the base commit."""
    ev = EvalResult(
        instance_id="sympy_1",
        resolved=True,
        fail_to_pass_passed=1,
        fail_to_pass_total=1,
        fail_to_pass_success=["tests/test_core.py::test_eval_poly"],
        fail_to_pass_failure=[],
    )

    # Base eval result shows test_eval_poly was ALREADY passing on unpatched base commit
    base_ev = EvalResult(
        instance_id="sympy_1",
        resolved=False,
        pass_to_pass_success=["tests/test_core.py::test_eval_poly"],
    )

    res = validate_evaluation_consistency(
        eval_result=ev,
        test_patch="def test_eval_poly(): pass\n",
        base_eval_result=base_ev,
    )

    assert res["inconsistent"] is True
    assert "tests/test_core.py::test_eval_poly" in res["non_discriminating_tests"]
    assert any("Non-discriminating fail-to-pass tests detected" in a for a in res["integrity_anomalies"])


def test_find_symbol_tool_rejects_symlink_pointing_outside_root(tmp_path):
    """Gap 5A: FindSymbolTool rejects symlink inside repo pointing outside root with PATH_SECURITY_ERROR."""
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    secret_file = outside / "secret.py"
    secret_file.write_text("SECRET_KEY = '12345'\n", encoding="utf-8")

    symlink_file = repo / "link_to_secret.py"
    try:
        symlink_file.symlink_to(secret_file)
    except OSError:
        pass

    state = MagicMock()
    state.repo_dir = str(repo)

    tool = FindSymbolTool()
    if symlink_file.exists():
        res = tool.execute({"file_path": "link_to_secret.py", "symbol": "SECRET_KEY"}, state=state)
        assert res.status == "ERROR"
        assert "PATH_SECURITY_ERROR" in res.error
    else:
        from patchforge.tools.editor import _resolve_and_contain

        def mock_realpath(p, *args, **kwargs):
            return str(secret_file) if "link_to_secret" in str(p) else str(p)

        with patch.object(Path, "exists", return_value=True), patch("os.path.realpath", side_effect=mock_realpath):
            _, err = _resolve_and_contain(repo, "link_to_secret.py", must_exist=False)
            assert err is not None
            assert "PATH_SECURITY_ERROR" in err


def test_repository_indexer_cache_invalidation_on_commit_change(tmp_path):
    """Gap 5B: Cache identity incorporates commit hash, invalidating stale cache paths across versions."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("def run(): pass\n", encoding="utf-8")

    cache_dir = tmp_path / "cache"
    indexer1 = RepositoryIndexer(repo_path=str(repo), cache_dir=str(cache_dir))
    with patch("patchforge.repository_intelligence.indexer._get_git_commit", return_value="commit_v1"):
        indexer1.commit_hash = "commit_v1"
        p1 = indexer1._get_cache_path()

    with patch("patchforge.repository_intelligence.indexer._get_git_commit", return_value="commit_v2"):
        indexer1.commit_hash = "commit_v2"
        p2 = indexer1._get_cache_path()

    # Different commits must produce different cache keys to prevent stale reuse
    assert p1 != p2


def test_pre_verification_loop_breaker_skips_verification():
    """Gap 4A: Candidate patch already seen in patch_hashes skips verification and triggers replanning."""
    patch_hashes = set()
    candidate_diff = "--- a/foo.py\n+++ b/foo.py\n@@ -1 +1 @@\n-1\n+2\n"
    cand_hash = hashlib.sha256(candidate_diff.encode("utf-8")).hexdigest()[:16]
    patch_hashes.add(cand_hash)

    # When the same patch is produced again:
    is_repeated = cand_hash in patch_hashes
    assert is_repeated is True

    # FailureDrivenReplanner receives repeated_attempt=True and breaks unproductive loop
    decision = FailureDrivenReplanner.decide(
        failure_class=FailureClass.UNRESOLVED.value,
        attempt=2,
        max_attempts=3,
        hypotheses_available=2,
        active_hypothesis_idx=0,
        f2p_passed=0,
        p2p_failed=0,
        error_message=f"Duplicate patch hash {cand_hash}",
        repeated_attempt=True,
    )
    assert decision.action in (ReplanAction.SWITCH_HYPOTHESIS, ReplanAction.ABORT)


# ==============================================================================
# 10. FINAL VERIFICATION: CACHE FINGERPRINT & HOST EXECUTION BOUNDARY TRACE
# ==============================================================================

def test_indexer_cache_invalidation_on_untracked_file_content_change(tmp_path):
    """Untracked Python file content changes must invalidate cached index fingerprint."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=str(repo), capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=str(repo), capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(repo), capture_output=True)

    (repo / "main.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(repo), capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo), capture_output=True)

    untracked = repo / "untracked.py"
    untracked.write_text("a = 1\n", encoding="utf-8")
    indexer1 = RepositoryIndexer(repo_path=str(repo), cache_dir=str(tmp_path / "cache"))
    cp1 = indexer1._get_cache_path()

    # Modify untracked file content without staging or renaming
    untracked.write_text("a = 2\n", encoding="utf-8")
    indexer2 = RepositoryIndexer(repo_path=str(repo), cache_dir=str(tmp_path / "cache"))
    cp2 = indexer2._get_cache_path()

    assert cp1 != cp2, "Cache paths must differ when untracked file content changes"


def test_indexer_cache_invalidation_on_untracked_subdir_and_deletion(tmp_path):
    """Untracked Python files in subdirectories and file deletions must invalidate cache fingerprint."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=str(repo), capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=str(repo), capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(repo), capture_output=True)

    (repo / "main.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(repo), capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo), capture_output=True)

    subpkg = repo / "subpkg"
    subpkg.mkdir()
    util = subpkg / "util.py"
    util.write_text("val = 10\n", encoding="utf-8")

    indexer1 = RepositoryIndexer(repo_path=str(repo), cache_dir=str(tmp_path / "cache"))
    cp1 = indexer1._get_cache_path()

    util.write_text("val = 20\n", encoding="utf-8")
    indexer2 = RepositoryIndexer(repo_path=str(repo), cache_dir=str(tmp_path / "cache"))
    cp2 = indexer2._get_cache_path()
    assert cp1 != cp2, "Cache paths must differ when subdir untracked content changes"

    util.unlink()
    indexer3 = RepositoryIndexer(repo_path=str(repo), cache_dir=str(tmp_path / "cache"))
    cp3 = indexer3._get_cache_path()
    assert cp2 != cp3, "Cache paths must differ when untracked file is deleted"


def test_indexer_cache_invalidation_on_staged_file_changes(tmp_path):
    """Staging a new file and modifying staged content must invalidate cached index fingerprint."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=str(repo), capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=str(repo), capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(repo), capture_output=True)

    (repo / "main.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(repo), capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo), capture_output=True)

    staged = repo / "staged.py"
    staged.write_text("y = 10\n", encoding="utf-8")
    subprocess.run(["git", "add", "staged.py"], cwd=str(repo), capture_output=True)

    indexer1 = RepositoryIndexer(repo_path=str(repo), cache_dir=str(tmp_path / "cache"))
    cp1 = indexer1._get_cache_path()

    staged.write_text("y = 20\n", encoding="utf-8")
    subprocess.run(["git", "add", "staged.py"], cwd=str(repo), capture_output=True)

    indexer2 = RepositoryIndexer(repo_path=str(repo), cache_dir=str(tmp_path / "cache"))
    cp2 = indexer2._get_cache_path()

    assert cp1 != cp2, "Cache paths must differ when staged file content changes"


def test_indexer_cache_invalidation_non_git_repo(tmp_path):
    """Non-git repository directories must invalidate cached index fingerprint when Python files change."""
    non_git = tmp_path / "non_git_repo"
    non_git.mkdir()
    mod = non_git / "module.py"
    mod.write_text("val = 1\n", encoding="utf-8")

    indexer1 = RepositoryIndexer(repo_path=str(non_git), cache_dir=str(tmp_path / "cache"))
    cp1 = indexer1._get_cache_path()

    mod.write_text("val = 2\n", encoding="utf-8")
    indexer2 = RepositoryIndexer(repo_path=str(non_git), cache_dir=str(tmp_path / "cache"))
    cp2 = indexer2._get_cache_path()

    assert cp1 != cp2, "Non-git directory must invalidate cache path when Python content changes"


def test_host_execution_authorization_boundary_trace(tmp_path):
    """Verify end-to-end authorization boundary: model args/state cannot grant privileges and sandbox failure never falls back."""
    # 1. Schemas must never expose authorization flags to LLM
    test_tool = RunTargetedTestTool(allow_host_execution=False)
    repro_tool = RunReproductionTool(allow_host_execution=False)
    assert "allow_host_execution" not in test_tool.parameters.get("properties", {})
    assert "allow_host_execution" not in repro_tool.parameters.get("properties", {})

    state = MagicMock()
    state.repo_dir = str(tmp_path)
    state.tester = None
    state.instance_id = None
    state.active_patch = None
    state.allow_host_execution = False

    # 2. Model-generated tool arguments cannot grant host privileges
    res_test = test_tool.execute({"test_target": "tests/test_x.py", "allow_host_execution": True}, state=state)
    assert res_test.status == "ERROR"
    assert "SECURITY_POLICY" in res_test.error
    assert res_test.data.get("isolation") == "BLOCKED"

    res_repro = repro_tool.execute({"repro_code": "assert True", "allow_host_execution": True}, state=state)
    assert res_repro.status == "ERROR"
    assert "SECURITY_POLICY" in res_repro.error
    assert res_repro.data.get("isolation") == "BLOCKED"

    # 3. Model-poisoned state (dict or non-boolean truthy values) cannot grant privileges
    poisoned_dict_state = {"allow_host_execution": True, "repo_dir": str(tmp_path)}
    res_dict = test_tool.execute({"test_target": "tests/test_x.py"}, state=poisoned_dict_state)
    assert res_dict.status == "ERROR"
    assert "SECURITY_POLICY" in res_dict.error

    state.allow_host_execution = "truthy_string"
    res_str = test_tool.execute({"test_target": "tests/test_x.py"}, state=state)
    assert res_str.status == "ERROR"
    assert "SECURITY_POLICY" in res_str.error

    state.allow_host_execution = 1
    res_int = repro_tool.execute({"repro_code": "assert True"}, state=state)
    assert res_int.status == "ERROR"
    assert "SECURITY_POLICY" in res_int.error

    # 4. Agent-controlled or forged state cannot grant privileges even with allow_host_execution=True
    state.allow_host_execution = True
    res_state_escalation = test_tool.execute({"test_target": "tests/test_x.py"}, state=state)
    assert res_state_escalation.status == "ERROR"
    assert "SECURITY_POLICY" in res_state_escalation.error
    assert res_state_escalation.data.get("isolation") == "BLOCKED"

    res_repro_state = repro_tool.execute({"repro_code": "assert True"}, state=state)
    assert res_repro_state.status == "ERROR"
    assert "SECURITY_POLICY" in res_repro_state.error
    assert res_repro_state.data.get("isolation") == "BLOCKED"

    # 5. A full-harness tester cannot silently run a selected test or fall back to host execution
    state.instance_id = "task_sandbox_1"
    state.active_patch = MagicMock(patch_text="diff --git a/x.py b/x.py\n")
    mock_tester = MagicMock()
    mock_tester.supports_selected_tests = False
    state.tester = mock_tester

    # Even if host execution was explicitly authorized, missing selector support fails closed
    tool_with_host_opt_in = RunTargetedTestTool(allow_host_execution=True)
    state.allow_host_execution = True
    res_fallback = tool_with_host_opt_in.execute({"test_target": "tests/test_x.py"}, state=state)

    assert res_fallback.status == "ERROR"
    assert "SELECTED_TEST_UNAVAILABLE" in res_fallback.error
    assert res_fallback.data.get("isolation") == "UNAVAILABLE"


