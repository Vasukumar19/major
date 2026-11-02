from types import SimpleNamespace

from patchforge.repair.assertion_failures import AssertionFailureExtractor
from patchforge.repair.replanner import FailureDrivenReplanner, ReplanAction
from patchforge.repair.residual import ResidualFailureRecord
from patchforge.verification.classifier import FailureClass


def test_extracts_pytest_assertion_with_test_identity_and_values():
    output = '''FAILED tests/test_headers.py::test_retry_after - AssertionError: assert "60" == "120"
____________________________ test_retry_after ____________________________
    def test_retry_after():
>       assert response.headers["Retry-After"] == "120"
E       AssertionError: assert "60" == "120"
'''
    failure = AssertionFailureExtractor.extract(output)[0]
    assert failure.test_id == "tests/test_headers.py::test_retry_after"
    assert failure.assertion == 'assert response.headers["Retry-After"] == "120"'
    assert failure.actual == '"60"'
    assert failure.expected == '"120"'


def test_residual_record_includes_assertion_evidence_in_micro_refinement_guidance():
    output = '''FAILED tests/test_headers.py::test_retry_after - AssertionError: assert "60" == "120"
>       assert response.headers["Retry-After"] == "120"
E       AssertionError: assert "60" == "120"
'''
    verdict = SimpleNamespace(
        f2p_passed=2,
        f2p_total=3,
        p2p_passed=10,
        p2p_total=10,
        resolved=False,
        error_message="Failing F2P test",
        test_summary={
            "fail_to_pass": {"success": ["a", "b"], "failure": ["tests/test_headers.py::test_retry_after"]},
            "test_output": output,
        },
    )
    record = ResidualFailureRecord.from_verdict("task", 2, verdict, patch_text="diff")
    assert record.is_partial_progress
    assert record.failing_assertions[0].expected == '"120"'
    guidance = record.format_corrective_guidance()
    assert "SMALLEST ACTIONABLE ASSERTION EVIDENCE" in guidance
    assert 'expected="120"' in guidance
    decision = FailureDrivenReplanner.decide(
        failure_class=FailureClass.PATCH_SEMANTICS.value,
        attempt=2,
        max_attempts=3,
        f2p_passed=2,
        residual_record=record,
    )
    assert decision.action is ReplanAction.REFINE_SEMANTICS
    assert 'expected="120"' in decision.prompt_guidance
