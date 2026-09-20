"""Unit tests for PatchForge AI V0.4.2:
Execution Evidence, Semantic Failure Analysis & Bounded Repair Refinement.
"""
from __future__ import annotations

import pytest

from patchforge.core.target import RepairTarget
from patchforge.issue.problem import Problem
from patchforge.pipeline.baseline import BaselineRepairEngine
from patchforge.reasoning.hypothesis import RefinedHypothesis
from patchforge.repair.patch import Patch
from patchforge.retrieval.evidence import ExecutionEvidence
from patchforge.verification.analyzer import FailureAnalyzer, RegressionCluster, SemanticDiagnosis


def test_execution_evidence_serialization():
    evidence = ExecutionEvidence(
        task_id="psf__requests-1963",
        target_tests_total=7,
        target_tests_passed=5,
        target_tests_failed=2,
        regression_tests_total=112,
        regression_tests_passed=112,
        regression_tests_failed=0,
        failure_class="PATCH_SEMANTICS",
        failed_target_tests=["test_redirects.py::test_requests_are_updated_each_time"],
        failed_regression_tests=[],
        passed_target_tests=["test_redirects.py::test_basic_redirect"],
        passed_regression_tests=["test_requests.py::test_http_get"],
        failure_messages=["AssertionError: assert 'POST' == 'GET'"],
        traceback="E   AssertionError: assert 'POST' == 'GET'\nE     - GET\nE     + POST",
        stdout="pytest output",
        stderr="",
        patch="diff --git a/requests/sessions.py b/requests/sessions.py...",
    )
    d = evidence.to_dict()
    assert d["task_id"] == "psf__requests-1963"
    assert d["target_tests_passed"] == 5
    assert d["failed_target_tests"] == ["test_redirects.py::test_requests_are_updated_each_time"]

    restored = ExecutionEvidence.from_dict(d)
    assert restored.task_id == evidence.task_id
    assert restored.target_tests_failed == 2
    assert restored.traceback == evidence.traceback


def test_failure_analyzer_target_failure():
    analyzer = FailureAnalyzer()
    raw_output = """
=================================== FAILURES ===================================
______________ TestRedirects.test_requests_are_updated_each_time _______________
    redirect_generator = session.resolve_redirects(r0, prep)
    for response in redirect_generator:
>       assert response.request.method == 'GET'
E       AssertionError: assert 'POST' == 'GET'
E         - GET
E         + POST
requests/sessions.py:184: AssertionError
"""
    details = analyzer.extract_test_details(
        raw_output, "test_requests.py::TestRedirects::test_requests_are_updated_each_time"
    )
    assert details["error_type"] == "AssertionError"
    assert "assert 'POST' == 'GET'" in details["error_message"]
    assert "- GET" in details["assertion_diff"]
    assert "requests/sessions.py:184" in details["target_locus"]


def test_failure_analyzer_regression_clustering():
    analyzer = FailureAnalyzer()
    raw_output = """
=================================== FAILURES ===================================
____________ RequestsTestCase.test_connection_error_invalid_domain _____________
>       except requests.packages.urllib3.exceptions.DecodeError as e:
E       NameError: name 'requests' is not defined
requests/sessions.py:467: NameError
_____________ RequestsTestCase.test_connection_error_invalid_port ______________
>       except requests.packages.urllib3.exceptions.DecodeError as e:
E       NameError: name 'requests' is not defined
requests/sessions.py:467: NameError
____________________ RequestsTestCase.test_invalid_url _________________________
>       except requests.packages.urllib3.exceptions.DecodeError as e:
E       NameError: name 'requests' is not defined
requests/sessions.py:467: NameError
"""
    evidence = ExecutionEvidence(
        task_id="psf__requests-2674",
        target_tests_total=12,
        target_tests_passed=12,
        target_tests_failed=0,
        regression_tests_total=142,
        regression_tests_passed=139,
        regression_tests_failed=3,
        failed_regression_tests=[
            "test_connection_error_invalid_domain",
            "test_connection_error_invalid_port",
            "test_invalid_url",
        ],
    )
    clusters = analyzer.cluster_regressions(evidence, raw_output)
    assert len(clusters) == 1
    assert clusters[0].error_type == "NameError"
    assert clusters[0].count == 3
    assert "requests/sessions.py" in clusters[0].file
    assert clusters[0].line == 467


def test_semantic_diagnosis_generation():
    analyzer = FailureAnalyzer()
    target = RepairTarget(
        file_path="requests/sessions.py",
        symbol="resolve_redirects",
        repository="psf/requests",
        line_start=84,
        line_end=184,
        verified_source="def resolve_redirects(): pass",
    )
    evidence = ExecutionEvidence(
        task_id="psf__requests-1963",
        target_tests_total=7,
        target_tests_passed=5,
        target_tests_failed=2,
        regression_tests_total=112,
        regression_tests_passed=112,
        regression_tests_failed=0,
        failed_target_tests=["test_requests_are_updated_each_time"],
        traceback="E AssertionError: assert 'POST' == 'GET'\nE   - GET\nE   + POST",
    )
    diagnosis = analyzer.analyze(evidence, target)
    assert diagnosis.failure_class == "SEMANTIC_MISUNDERSTANDING"
    assert "test_requests_are_updated_each_time" in diagnosis.failed_tests
    assert diagnosis.regression_scope == "None"
    assert "=== EXECUTION EVIDENCE ===" in diagnosis.diagnostics_text
    assert "=== SEMANTIC DIAGNOSIS ===" in diagnosis.diagnostics_text
    assert "AssertionError" in diagnosis.diagnostics_text


def test_failure_routing_target_vs_regression():
    analyzer = FailureAnalyzer()
    target = RepairTarget(file_path="foo.py", symbol="bar")

    # Pure target failure -> SEMANTIC_MISUNDERSTANDING
    target_fail_ev = ExecutionEvidence(
        target_tests_total=5,
        target_tests_passed=4,
        target_tests_failed=1,
        regression_tests_total=50,
        regression_tests_passed=50,
        failed_target_tests=["test_a"],
        traceback="E AssertionError: assert 1 == 2",
    )
    diag_target = analyzer.analyze(target_fail_ev, target)
    assert diag_target.failure_class == "SEMANTIC_MISUNDERSTANDING"

    # Pure regression failure -> REGRESSION
    reg_fail_ev = ExecutionEvidence(
        target_tests_total=5,
        target_tests_passed=5,
        target_tests_failed=0,
        regression_tests_total=50,
        regression_tests_passed=45,
        failed_regression_tests=["test_reg_1"],
        traceback="E NameError: name 'x' is not defined",
    )
    diag_reg = analyzer.analyze(reg_fail_ev, target)
    assert diag_reg.failure_class == "REGRESSION"

    # Mixed failure -> BLAST_RADIUS
    mixed_ev = ExecutionEvidence(
        target_tests_total=5,
        target_tests_passed=3,
        target_tests_failed=2,
        regression_tests_total=50,
        regression_tests_passed=45,
        failed_target_tests=["test_t1"],
        failed_regression_tests=["test_r1"],
    )
    diag_mixed = analyzer.analyze(mixed_ev, target)
    assert diag_mixed.failure_class == "BLAST_RADIUS"


def test_refinement_context_construction():
    engine = BaselineRepairEngine()
    problem = Problem(
        instance_id="psf__requests-1963",
        repo="psf/requests",
        base_commit="abc",
        problem_statement="Session.resolve_redirects copies original request",
    )
    target = RepairTarget(
        file_path="requests/sessions.py",
        symbol="resolve_redirects",
        repository="psf/requests",
        line_start=84,
        line_end=184,
        verified_source="def resolve_redirects(self, resp, req):\n    pass\n",
    )
    original_patch = Patch(
        patch_text="--- a/requests/sessions.py\n+++ b/requests/sessions.py\n@@ -123,4 +123,4 @@\n- method = 'GET'\n+ method = req.method\n"
    )
    evidence = ExecutionEvidence(
        task_id=problem.instance_id,
        target_tests_total=7,
        target_tests_passed=5,
        target_tests_failed=2,
        failed_target_tests=["test_requests_are_updated_each_time"],
        traceback="E AssertionError: assert 'POST' == 'GET'",
    )
    diagnosis = FailureAnalyzer().analyze(evidence, target)
    refined_hypothesis = RefinedHypothesis(
        original_hypothesis="Original hypothesis",
        execution_evidence_summary="5/7 passed",
        refined_hypothesis="Refined hypothesis text",
    )

    ctx = engine.build_refinement_context(
        problem=problem,
        target=target,
        original_patch=original_patch,
        evidence=evidence,
        diagnosis=diagnosis,
        refined_hypothesis=refined_hypothesis,
    )

    # Validate all 9 standard sections from Section 17 are present
    assert "=== ISSUE ===" in ctx
    assert "=== REPAIR TARGET ===" in ctx
    assert "=== VERIFIED EDITABLE SOURCE ===" in ctx
    assert "=== ORIGINAL HYPOTHESIS ===" in ctx
    assert "=== ORIGINAL PATCH ===" in ctx
    assert "=== EXECUTION EVIDENCE ===" in ctx
    assert "=== SEMANTIC DIAGNOSIS ===" in ctx
    assert "=== REFINED HYPOTHESIS ===" in ctx
    assert "=== REPAIR CONSTRAINTS ===" in ctx
    assert "requests/sessions.py" in ctx


def test_repeated_failure_and_budget_guards():
    """Verifies hard refinement budget and repeated-failure guard."""
    class FakeProvider:
        def __init__(self):
            self.calls = 0

        def generate_one(self, prompt, system=""):
            self.calls += 1
            return type("Resp", (), {
                "text": '{"action": {"name": "apply_patch", "arguments": {"patch_text": "### file.py\\n<<<<<<< SEARCH\\nline\\n=======\\nfixed line\\n>>>>>>> REPLACE"}}}',
                "input_tokens": 100,
                "output_tokens": 50,
            })()

    class FakeVerdict:
        def __init__(self, resolved=False, f2p_p=1, f2p_t=2, p2p_p=10, p2p_t=10):
            self.resolved = resolved
            self.fail_to_pass_passed = f2p_p
            self.fail_to_pass_total = f2p_t
            self.pass_to_pass_passed = p2p_p
            self.pass_to_pass_total = p2p_t
            self.fail_to_pass_failure = ["test_target_failing"]
            self.fail_to_pass_success = ["test_target_ok"]
            self.pass_to_pass_failure = []
            self.pass_to_pass_success = [f"test_p_{i}" for i in range(10)]
            self.infra_failure = False
            self.error = ""
            self.test_output = "E AssertionError: failed"
            self.stdout = ""
            self.stderr = ""

        def to_dict(self):
            return {"resolved": self.resolved}

    class FakeTester:
        def __init__(self):
            self.runs = 0

        def run(self, instance_id, patch_text):
            self.runs += 1
            # Always return identical failure
            return FakeVerdict(resolved=False)

    provider = FakeProvider()
    engine = BaselineRepairEngine(provider=provider)
    problem = Problem(instance_id="test_task", problem_statement="test issue", repo="org/repo", base_commit="1")
    target = RepairTarget(file_path="file.py", symbol="foo", verification_status=True, verified_source="line\n")
    engine.localize = lambda prob, r_dir: target

    # Mock ApplyPatchTool in the engine's run execution
    import patchforge.pipeline.baseline as bl
    orig_apply = bl.ApplyPatchTool

    class FakeApplyPatchTool:
        def __init__(self, repo_dir=""):
            pass

        def execute(self, args):
            return type("Res", (), {
                "status": "SUCCESS",
                "error": "",
                "data": {"patch": {"match_tier": "EXACT"}, "diff": "fake diff", "files_changed": ["file.py"]},
            })()

    bl.ApplyPatchTool = FakeApplyPatchTool
    tester = FakeTester()

    try:
        res = engine.run(problem, repo_dir=".", tester=tester, max_retries=1, max_refinements=2)
        # In V0.4.3, failure_history decouples from cycle 0, so cycle 1 and cycle 2 execute
        # and cycle 2 triggers REFINEMENT_EXHAUSTED upon encountering cycle 1's signature.
        assert res.failure_class == "REFINEMENT_EXHAUSTED"
        assert res.refinement_cycles == 2
        assert tester.runs <= 3
    finally:
        bl.ApplyPatchTool = orig_apply
