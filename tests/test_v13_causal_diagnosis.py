"""Unit tests for PatchForge V1.3 Causal Diagnosis & Behavioral Repair Completeness."""
import ast
import pytest

from patchforge.diagnosis.causal_trace import (
    BehavioralTrace,
    FirstDivergenceLocator,
    TraceEvent,
    TraceEventType,
)
from patchforge.diagnosis.diagnosis import (
    CausalPrediction,
    CompetingDiagnosisEngine,
    CompetingDiagnosisResult,
    DiagnosisHypothesis,
)
from patchforge.experiment.planner import ExperimentPlanner
from patchforge.experiment.probe import ProbeType
from patchforge.repair.replanner import FailureDrivenReplanner, ReplanAction


def test_first_divergence_locator_pinpoints_preceding_branch():
    source_code = '''def process_response(resp, stream=False):
    if not resp:
        return None
    if stream:
        data = resp.raw.read()
    else:
        data = resp.content
    result = transform(data)
    assert result is not None
    return result
'''
    # Assertion failure occurs at line 9, but divergence is at line 4 (if stream)
    trace = FirstDivergenceLocator.locate_divergence(
        source_code=source_code,
        target_symbol="process_response",
        file_path="requests/models.py",
        traceback_lines=[9],
        problem_statement="Streaming mode decoding fails on raw read",
    )

    assert trace.target_symbol == "process_response"
    assert trace.assertion_failure_line == 9
    assert trace.first_divergence_line is not None
    # First divergence should be the branch governing the path before assertion
    assert trace.first_divergence_line in (2, 4)
    assert trace.divergence_transition != ""
    assert len(trace.events) >= 3

    prompt_summary = trace.format_for_prompt()
    assert "CAUSAL BEHAVIORAL TRACE" in prompt_summary
    assert "FIRST BEHAVIORAL DIVERGENCE" in prompt_summary


def test_causal_prediction_deterministic_elimination():
    trace = BehavioralTrace(
        target_symbol="resolve_redirect",
        file_path="requests/sessions.py",
        first_divergence_line=5,
        divergence_transition="At line 5, redirects to different domain strip auth headers",
        events=[
            TraceEvent(
                node_id="ev_1",
                event_type=TraceEventType.BRANCH,
                line=5,
                code_snippet="if self.should_strip_auth(old_url, new_url):",
                predicate_evaluated="should_strip_auth is True",
            ),
            TraceEvent(
                node_id="ev_2",
                event_type=TraceEventType.MUTATION,
                line=7,
                code_snippet="del headers['Authorization']",
                state_delta={"Authorization": "DELETED"},
            ),
        ],
    )

    h_flawed = DiagnosisHypothesis(
        id="A",
        cause="Believed auth header was not deleted.",
        prediction=CausalPrediction(
            expected_branch="should_strip_auth is False",
            forbidden_events=["del headers['Authorization']"],  # Contradicts trace ev_2!
            discriminating_variable="should_strip_auth",
        ),
        confidence=0.85,
    )

    h_correct = DiagnosisHypothesis(
        id="B",
        cause="Subdomain dot scope should not be stripped.",
        prediction=CausalPrediction(
            expected_branch="strip auth headers",
            discriminating_variable="should_strip_auth",
        ),
        confidence=0.75,
    )

    CompetingDiagnosisEngine._eliminate_and_rank_hypotheses(
        hypotheses=[h_flawed, h_correct],
        candidate_profiles=[],
        failure_evidence=None,
        behavior_map=None,
        causal_trace=trace,
    )

    # h_flawed must have contradiction count >= 1 and penalty applied
    assert h_flawed.contradiction_count >= 1
    assert any("Forbidden event" in err for err in h_flawed.contradicting_evidence)
    # h_correct should be selected or have higher causal support
    assert h_correct.counterfactual_score > h_flawed.counterfactual_score


def test_information_directed_probing():
    h1 = DiagnosisHypothesis(
        id="A",
        cause="Encoding defaults to ISO-8859-1",
        prediction=CausalPrediction(
            discriminating_variable="encoding",
            expected_output_state={"encoding": "ISO-8859-1"},
        ),
    )
    h2 = DiagnosisHypothesis(
        id="B",
        cause="Encoding detected as utf-8",
        prediction=CausalPrediction(
            discriminating_variable="encoding",
            expected_output_state={"encoding": "utf-8"},
        ),
    )

    diag = CompetingDiagnosisResult(
        hypotheses=[h1, h2],
        selected_hypothesis_idx=0,
    )

    probes = ExperimentPlanner.plan_experiment(
        diagnosis=diag,
        target_file="requests/models.py",
        target_symbol="iter_content",
        target_line=10,
    )

    assert len(probes) >= 2
    # The variable probe must specifically target the discriminating variable 'encoding'
    var_probe = next((p for p in probes if p.probe_type == ProbeType.VARIABLE_VALUE), None)
    assert var_probe is not None
    assert var_probe.variable_name == "encoding"
    assert var_probe.expected_if_h1_true == "ISO-8859-1"
    assert var_probe.expected_if_h2_true == "utf-8"


def test_behavioral_repair_completeness_replanning():
    # When patch achieves partial success (e.g. 9/10 tests passed)
    decision = FailureDrivenReplanner.decide(
        failure_class="PATCH_SEMANTICS",
        attempt=1,
        max_attempts=3,
        hypotheses_available=2,
        active_hypothesis_idx=0,
        f2p_passed=9,
        p2p_failed=0,
        error_message="AssertionError: assert 'chunked' in headers",
    )

    assert decision.action == ReplanAction.REFINE_SEMANTICS
    assert "BEHAVIORAL REPAIR COMPLETENESS" in decision.prompt_guidance
    assert "9 test assertions" in decision.prompt_guidance
    assert "DO NOT discard your logic" in decision.prompt_guidance
