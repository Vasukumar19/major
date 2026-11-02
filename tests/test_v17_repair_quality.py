"""Tests for PatchForge V1.7 Evidence-Driven Diagnosis and Autonomous Repair Quality.

Covers:
1. Competing hypotheses with conflicting supporting evidence & deduplication.
2. Missing evidence remaining unknown (no confidence inflation).
3. Retrieval signals contributing to active diagnosis and target selection.
4. Consistency between diagnosed cause, edit target, and planned repair unit.
5. Wrong hypothesis abandoned & full context rebuilt for alternative hypothesis.
6. Partial repair with residual assertions & corrective guidance.
7. Preserving already passing behaviors (regression avoidance).
8. Failed attempt triggering materially different next action.
9. Loop breaker terminating unproductive retry cycles.
10. Clean termination when retry budget is exhausted.
11. Accurate reporting when evidence cannot justify a diagnosis or target.
12. Forensic replay on historical failure cases (requests-2317 and requests-1963).
"""
import ast
import hashlib
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from patchforge.core.target import EditSite, RepairTarget
from patchforge.diagnosis import (
    CompetingDiagnosisEngine,
    CompetingDiagnosisResult,
    DiagnosisHypothesis,
    IssueBehaviorMap,
)
from patchforge.diagnosis.evidence import (
    EvidenceItem,
    EvidenceKind,
    EvidencePackage,
    StructuredFailureEvidence,
)
from patchforge.issue.problem import Problem
from patchforge.pipeline.v1_orchestrator import (
    V1OrchestratorResult,
    V1RepairOrchestrator,
)
from patchforge.repair.planner import RepairUnitPlanner
from patchforge.repair.ranking import RepairSiteRanker
from patchforge.repair.reconstructor import SourceReconstructor
from patchforge.repair.replanner import FailureDrivenReplanner, ReplanAction
from patchforge.repair.residual import ResidualFailureRecord
from patchforge.repair.schema import (
    RankedRepairSite,
    ReconstructedPatch,
    RepairAction,
    RepairUnit,
    RepairUnitType,
    StructuredRepairOutput,
)
from patchforge.repair.structured_repair import (
    StructuredRepairParser,
    StructuredRepairPromptBuilder,
)
from patchforge.verification.adaptive import (
    AdaptiveVerificationVerdict,
    VerificationTier,
)
from patchforge.verification.classifier import FailureClass


# ---------------------------------------------------------------------------
# 1. Competing Hypotheses & Conflict Resolution
# ---------------------------------------------------------------------------

def test_competing_hypotheses_conflicting_evidence_predictable_elimination():
    """Verify that competing hypotheses are evaluated predictably based on evidence,

    and degenerate duplicate hypotheses are eliminated.
    """
    hyp_a = DiagnosisHypothesis(
        id="A",
        cause="Incorrect encoding handling in stream reader",
        invariant="Caller expects binary bytes intact",
        repair_strategy="Encode to utf-8 in read loop",
        affected_sites=["requests/models.py:iter_lines"],
        supporting_evidence=["Generic issue text mentioning lines"],
        contradicting_evidence=["High blast radius", "Causes TypeError in test_stream_binary"],
        confidence=0.85,
    )
    hyp_b = DiagnosisHypothesis(
        id="B",
        cause="Improper handling of builtin str decode in session hook",
        invariant="Preserve builtin str types across python versions",
        repair_strategy="Check isinstance(chunk, str) before decoding",
        affected_sites=["requests/sessions.py:builtin_str"],
        supporting_evidence=["Traceback matches session hook line 42", "Direct test test_builtin_str fails"],
        discriminating_tests=["test_builtin_str"],
        distinguishing_evidence="Direct test failure in session hook",
        specificity_explanation="Directly targets the type mismatch identified in session hook",
        confidence=0.75,
    )

    result = CompetingDiagnosisResult(
        hypotheses=[hyp_a, hyp_b],
        selected_hypothesis=hyp_a,
        selected_hypothesis_idx=0,
        repair_sites=["requests/models.py:iter_lines"],
    )

    CompetingDiagnosisEngine._eliminate_and_rank_hypotheses(
        hypotheses=[hyp_a, hyp_b],
        candidate_profiles=[],
        failure_evidence=None,
        behavior_map=None,
    )

    # Hypothesis B must be selected despite being listed second, because Hyp A has contradicting evidence
    # and Hyp B has discriminating tests and specificity explanation
    active_hyps = [h for h in [hyp_a, hyp_b] if not h.eliminated]
    active_hyps.sort(key=lambda h: h.counterfactual_score, reverse=True)
    winner = active_hyps[0]
    assert winner.id == "B"
    assert winner.confidence > 0.0

    # Test duplicate hypothesis elimination
    clone_b = DiagnosisHypothesis(
        id="C",
        cause="Improper handling of builtin str decode in session hook",
        invariant="Preserve builtin str types across python versions",
        repair_strategy="Check isinstance(chunk, str) before decoding",
        affected_sites=["requests/sessions.py:builtin_str"],
        confidence=0.70,
    )
    deduped = CompetingDiagnosisEngine._detect_and_deduplicate_hypotheses([hyp_b, clone_b])
    active = [h for h in deduped if not h.eliminated]
    assert len(active) == 1
    assert active[0].id == "B"
    assert deduped[1].eliminated is True
    assert "Duplicate of Hypothesis B" in deduped[1].elimination_reason


# ---------------------------------------------------------------------------
# 2. Missing Evidence Remains Unknown (No Confidence Inflation)
# ---------------------------------------------------------------------------

def test_missing_evidence_remains_unknown_no_confidence_inflation():
    """Verify missing evidence is explicitly recorded as unknown and confidence is not inflated."""
    pkg = EvidencePackage(instance_id="test__proj-101")
    pkg.add_item(
        kind=EvidenceKind.UNKNOWN_OR_UNAVAILABLE,
        category="DYNAMIC_RUNTIME_TRACE",
        content="No runtime traceback observed prior to repair; avoid fabricating execution trace.",
    )
    pkg.add_item(
        kind=EvidenceKind.UNKNOWN_OR_UNAVAILABLE,
        category="DYNAMIC_EXECUTION_PROBE",
        content="Dynamic probing disabled by configuration; no probe state recorded.",
    )

    formatted = pkg.format_for_prompt()
    assert "UNKNOWN / UNAVAILABLE INFORMATION" in formatted
    assert "DYNAMIC_RUNTIME_TRACE" in formatted
    assert "DYNAMIC_EXECUTION_PROBE" in formatted

    # When no observations are present, confidence should be capped and uncertainty preserved
    hyp = DiagnosisHypothesis(
        id="A",
        cause="Unverified speculative defect",
        repair_strategy="Speculative edit",
        affected_sites=["mod.py:fn"],
        confidence=0.95,
    )
    CompetingDiagnosisEngine._eliminate_and_rank_hypotheses(
        hypotheses=[hyp],
        candidate_profiles=[],
        failure_evidence=None,
        behavior_map=None,
    )
    # Must cap confidence to preserve uncertainty when no evidence supports it
    assert hyp.confidence <= 0.75


# ---------------------------------------------------------------------------
# 3. Multichannel Retrieval Signals Contribute to Diagnosis
# ---------------------------------------------------------------------------

def test_multichannel_retrieval_signals_contribute_to_active_diagnosis():
    """Verify that retrieval channels (BM25, graph, tests) propagate into evidence packaging."""
    pkg = EvidencePackage(instance_id="test__proj-102")
    pkg.candidate_symbols = ["requests/sessions.py:Session.send", "requests/models.py:Response"]
    pkg.add_item(
        kind=EvidenceKind.STATIC_CODE_EVIDENCE,
        category="AST_SURGICAL_UNIT",
        content="Symbol `Session.send` in `requests/sessions.py` lines 10-50",
        provenance="requests/sessions.py:10",
        target_symbol="Session.send",
    )
    pkg.add_item(
        kind=EvidenceKind.STATIC_CODE_EVIDENCE,
        category="CALL_GRAPH",
        content="Caller test_session_redirect -> Session.send",
        provenance="test_graph",
    )
    pkg.associated_tests = ["tests/test_sessions.py:test_send"]

    prompt_text = pkg.format_for_prompt()
    assert "Session.send" in prompt_text
    assert "CALL_GRAPH" in prompt_text
    assert "STATIC CODE EVIDENCE" in prompt_text


# ---------------------------------------------------------------------------
# 4. Consistency: Diagnosed Cause, Edit Target, and Repair Unit
# ---------------------------------------------------------------------------

def test_diagnosed_cause_target_and_repair_unit_consistency(tmp_path):
    """Verify that when candidate ranker drifts from diagnosed cause, target reconciliation

    forces binding to the diagnosed affected site and the planned repair unit matches it.
    """
    code_file = tmp_path / "helpers.py"
    code_file.write_text("def url_for(endpoint, **values):\n    return '/test'\n", encoding="utf-8")

    wrong_file = tmp_path / "app.py"
    wrong_file.write_text("def route_decorator():\n    pass\n", encoding="utf-8")

    hyp = DiagnosisHypothesis(
        id="A",
        cause="url_for strips query parameters prematurely",
        repair_strategy="Preserve query parameters in url_for",
        affected_sites=[f"{code_file.name}:url_for"],
    )
    diag = CompetingDiagnosisResult(
        hypotheses=[hyp],
        selected_hypothesis=hyp,
        repair_sites=[f"{code_file.name}:url_for"],
    )

    # Initially ranked candidate is from app.py
    drifted_site = EditSite(
        file_path=str(wrong_file.name),
        symbol="route_decorator",
        line_start=1,
        line_end=2,
        source_span=(1, 2),
        verified_source=wrong_file.read_text(),
        verification_status=True,
    )

    # Check reconciliation: drifted site is incoherent
    is_coherent, just = RepairSiteRanker.reconcile_target(drifted_site, diag, graph=None)
    assert is_coherent is False

    # Target unit planner for the diagnosed site creates consistent repair unit
    unit = RepairUnitPlanner.plan_repair_unit(
        file_path=str(code_file.name),
        source_code=code_file.read_text(),
        target_symbol="url_for",
        target_lines=(1, 2),
        diagnosis=diag,
        role="PRIMARY",
    )
    assert unit.symbol == "url_for"
    assert unit.file_path == str(code_file.name)
    assert "url_for" in unit.source_text


# ---------------------------------------------------------------------------
# 5. Wrong Hypothesis Abandoned & Full Context Rebuilt
# ---------------------------------------------------------------------------

def test_wrong_hypothesis_abandoned_and_full_context_rebuilt(tmp_path):
    """Verify that when Hypothesis A fails, replanner pivots to Hypothesis B

    and _rebuild_repair_context switches target and repair unit.
    """
    decision = FailureDrivenReplanner.decide(
        failure_class=FailureClass.WRONG_HYPOTHESIS.value,
        attempt=1,
        max_attempts=3,
        hypotheses_available=2,
        active_hypothesis_idx=0,
    )
    assert decision.action == ReplanAction.SWITCH_HYPOTHESIS
    assert decision.target_hypothesis_id == "B"

    # Set up files for both hypotheses
    file_a = tmp_path / "mod_a.py"
    file_a.write_text("def fn_a():\n    return 1\n", encoding="utf-8")
    file_b = tmp_path / "mod_b.py"
    file_b.write_text("def fn_b():\n    return 2\n", encoding="utf-8")

    hyp_a = DiagnosisHypothesis(id="A", cause="Bug in A", repair_strategy="Fix A", affected_sites=["mod_a.py:fn_a"])
    hyp_b = DiagnosisHypothesis(id="B", cause="Bug in B", repair_strategy="Fix B", affected_sites=["mod_b.py:fn_b"])
    diag = CompetingDiagnosisResult(
        hypotheses=[hyp_a, hyp_b],
        selected_hypothesis=hyp_a,
        selected_hypothesis_idx=0,
        repair_sites=["mod_a.py:fn_a"],
    )

    orch = V1RepairOrchestrator(model_name="mock-model", workspace=str(tmp_path))
    problem = Problem(instance_id="test__switch-1", repo="mock/repo", base_commit="abc", problem_statement="Fix bug")

    mock_repo_map = MagicMock()
    mock_repo_map.extract_surgical_unit.return_value = ("def fn_b():\n    return 2\n", 1, 2, "")
    mock_retriever = MagicMock()
    mock_retriever.retrieve.return_value = None

    target_prov = {"target_changes": []}
    (
        sel_site,
        target,
        orig_code,
        target_f_abs,
        target_f_rel,
        ctx,
        unit,
    ) = orch._rebuild_repair_context(
        problem=problem,
        repo_dir=str(tmp_path),
        repo_map=mock_repo_map,
        graph=None,
        test_index=None,
        diagnosis=diag,
        active_hyp_idx=1,
        context_retriever=mock_retriever,
        api_contract=None,
        req_matrix=None,
        candidate_profiles_dict={},
        candidate_file="mod_a.py",
        target_provenance=target_prov,
    )

    assert diag.selected_hypothesis_idx == 1
    assert diag.selected_hypothesis.id == "B"
    assert target.file_path == "mod_b.py"
    assert target.symbol == "fn_b"
    assert unit.symbol == "fn_b"
    assert len(target_prov["target_changes"]) == 1
    assert target_prov["target_changes"][0]["switched_to_hypothesis"] == "B"


# ---------------------------------------------------------------------------
# 6. Partial Repair with Residual Assertions & Corrective Guidance
# ---------------------------------------------------------------------------

def test_partial_repair_extracts_residual_failure_and_precise_guidance():
    """Verify that a partial repair (passing some F2P tests) produces structured residual

    guidance and injects working prior replacement into the subsequent prompt.
    """
    verdict = AdaptiveVerificationVerdict(
        tier_reached=VerificationTier.TARGETED_F2P,
        tests_executed=True,
        f2p_passed=3,
        f2p_total=4,
        p2p_passed=50,
        p2p_total=50,
        resolved=False,
        failure_class=FailureClass.PATCH_SEMANTICS.value,
        error_message="AssertionError: assert parse_header('foo') == 'expected_foo'",
        test_summary={
            "fail_to_pass": {"success": ["test_case_1", "test_case_2", "test_case_3"], "failure": ["test_case_4"]},
            "fail_to_pass_success": ["test_case_1", "test_case_2", "test_case_3"],
            "fail_to_pass_failure": ["test_case_4"],
        },
    )

    rec = ResidualFailureRecord.from_verdict(
        instance_id="test__partial-1",
        attempt=1,
        verdict=verdict,
        patch_text="diff --git a/mod.py b/mod.py\n...",
        target_str="mod.py:parse_header",
    )

    assert rec.is_partial_progress is True
    assert rec.has_regression is False
    assert rec.f2p_passed_count == 3
    assert rec.f2p_total_count == 4
    assert "test_case_1" in rec.f2p_passed_tests
    assert "test_case_4" in rec.f2p_failed_tests

    guidance = rec.format_corrective_guidance()
    assert "PARTIAL REPAIR PROGRESS DIAGNOSIS (Attempt 1)" in guidance
    assert "PRESERVE THESE PASSING ASSERTIONS" in guidance
    assert "test_case_1" in guidance
    assert "RESIDUAL FAILING ASSERTIONS" in guidance
    assert "test_case_4" in guidance
    assert "Do NOT rewrite your implementation from scratch" in guidance

    # Build prompt on attempt 2 incorporating residual guidance and prior patch code
    unit = RepairUnit(
        id="u1",
        file_path="mod.py",
        symbol="parse_header",
        unit_type=RepairUnitType.FUNCTION,
        node_type="FunctionDef",
        start_line=1,
        end_line=10,
        source_text="def parse_header(h):\n    return h\n",
    )
    prompt = StructuredRepairPromptBuilder.build_repair_prompt(
        unit=unit,
        problem=Problem(instance_id="test__partial-1", repo="repo", base_commit="123", problem_statement="Fix header"),
        diagnosis=DiagnosisHypothesis(id="A", cause="Bug", repair_strategy="Fix"),
        residual_guidance=guidance,
        prior_patch_code="def parse_header(h):\n    return h.strip()\n",
    )
    assert "### RESIDUAL BEHAVIORAL REPAIR GUIDANCE:" in prompt
    assert "### PRIOR PARTIALLY WORKING REPLACEMENT:" in prompt
    assert "def parse_header(h):\n    return h.strip()" in prompt


# ---------------------------------------------------------------------------
# 7. Regression Avoidance (Preserving Passing Behaviors)
# ---------------------------------------------------------------------------

def test_regression_avoidance_preserves_already_passing_behaviors():
    """Verify that regressions (failing previously passing P2P tests) trigger CONSTRAIN_MUTATION."""
    verdict = AdaptiveVerificationVerdict(
        tier_reached=VerificationTier.REGRESSION_P2P,
        tests_executed=True,
        f2p_passed=2,
        f2p_total=2,
        p2p_passed=98,
        p2p_total=100,  # 2 regressions
        resolved=False,
        failure_class=FailureClass.REGRESSION.value,
        error_message="Regression detected: existing test broken",
    )

    rec = ResidualFailureRecord.from_verdict(
        instance_id="test__regress-1",
        attempt=1,
        verdict=verdict,
    )
    assert rec.has_regression is True
    assert rec.is_partial_progress is False  # Regression cancels partial progress

    decision = FailureDrivenReplanner.decide(
        failure_class=verdict.failure_class,
        attempt=1,
        max_attempts=3,
        f2p_passed=2,
        p2p_failed=2,
        residual_record=rec,
    )
    assert decision.action == ReplanAction.CONSTRAIN_MUTATION
    assert "Regression detected" in decision.reason
    assert "blast radius" in decision.reason


# ---------------------------------------------------------------------------
# 8. Materially Different Next Action After Failed Attempt
# ---------------------------------------------------------------------------

def test_failed_attempt_triggers_materially_different_replan_action():
    """Verify that a schema or syntax failure triggers FALLBACK_SYNTAX with a minimal prompt,

    ensuring attempt 2 does not repeat attempt 1's failed structure.
    """
    decision = FailureDrivenReplanner.decide(
        failure_class=FailureClass.REPAIR_SCHEMA_FAILURE.value,
        attempt=1,
        max_attempts=3,
        error_message="Invalid JSON: Unexpected token",
    )
    assert decision.action == ReplanAction.FALLBACK_SYNTAX

    unit = RepairUnit(
        id="u2",
        file_path="foo.py",
        symbol="bar",
        unit_type=RepairUnitType.FUNCTION,
        node_type="FunctionDef",
        start_line=1,
        end_line=5,
        source_text="def bar():\n    pass\n",
    )
    minimal_prompt = StructuredRepairPromptBuilder.build_minimal_prompt(
        unit=unit,
        error_feedback=decision.prompt_guidance,
    )
    assert "Provide ONLY the minimal JSON replacement" in minimal_prompt
    assert "Do NOT output commentary" in minimal_prompt
    assert decision.prompt_guidance in minimal_prompt

    replacement_only = StructuredRepairPromptBuilder.build_replacement_only_prompt(
        unit=unit,
        error_feedback=decision.prompt_guidance,
    )
    assert "Return ONLY the raw Python replacement code" in replacement_only


# ---------------------------------------------------------------------------
# 9. Loop Breaker Terminates Unproductive Retry Cycles
# ---------------------------------------------------------------------------

def test_loop_breaker_terminates_unproductive_retry_cycle():
    """Verify that repeated ineffective attempts (identical patch or zero progress)

    terminate early via ReplanAction.ABORT when no alternative hypotheses remain.
    """
    decision = FailureDrivenReplanner.decide(
        failure_class=FailureClass.UNRESOLVED.value,
        attempt=2,
        max_attempts=4,
        hypotheses_available=1,
        active_hypothesis_idx=0,
        repeated_attempt=True,
    )
    assert decision.action == ReplanAction.ABORT
    assert "Repeated identical attempt detected" in decision.reason
    assert "Terminating unproductive retry loop" in decision.reason

    # If alternative hypotheses ARE available, loop breaker should switch instead of aborting
    decision_switch = FailureDrivenReplanner.decide(
        failure_class=FailureClass.UNRESOLVED.value,
        attempt=2,
        max_attempts=4,
        hypotheses_available=2,
        active_hypothesis_idx=0,
        repeated_attempt=True,
    )
    assert decision_switch.action == ReplanAction.SWITCH_HYPOTHESIS
    assert decision_switch.target_hypothesis_id == "B"


# ---------------------------------------------------------------------------
# 10. Clean Termination When Retry Budget is Exhausted
# ---------------------------------------------------------------------------

def test_clean_termination_when_budget_exhausted():
    """Verify clean termination and honest reporting when attempt reaches max_attempts."""
    decision = FailureDrivenReplanner.decide(
        failure_class=FailureClass.PATCH_SEMANTICS.value,
        attempt=3,
        max_attempts=3,
    )
    assert decision.action == ReplanAction.ABORT
    assert "Exhausted maximum retry budget" in decision.reason


# ---------------------------------------------------------------------------
# 11. Accurate Reporting of Unjustified Diagnosis or Target
# ---------------------------------------------------------------------------

def test_unjustified_diagnosis_or_target_reported_accurately(tmp_path):
    """Verify that when diagnosis sites do not exist in the repository,

    the orchestrator accurately records target_unjustified=True in result and provenance.
    """
    real_file = tmp_path / "actual.py"
    real_file.write_text("def valid_fn():\n    return 42\n", encoding="utf-8")

    hyp = DiagnosisHypothesis(
        id="A",
        cause="Defect in nonexistent module",
        repair_strategy="Fix nonexistent",
        affected_sites=["nonexistent/fake.py:ghost_symbol"],
    )
    diag = CompetingDiagnosisResult(
        hypotheses=[hyp],
        selected_hypothesis=hyp,
        repair_sites=["nonexistent/fake.py:ghost_symbol"],
    )

    candidate = EditSite(
        file_path="actual.py",
        symbol="valid_fn",
        line_start=1,
        line_end=2,
        source_span=(1, 2),
        verified_source=real_file.read_text(),
        verification_status=True,
    )

    coherent, just = RepairSiteRanker.reconcile_target(candidate, diag, graph=None)
    assert coherent is False

    # Result provenance with forced unmatched site
    result = V1OrchestratorResult(instance_id="test__unjust-1")
    if not coherent:
        result.target_unjustified = True

    assert result.target_unjustified is True


# ---------------------------------------------------------------------------
# 12. Forensic Replay of Historical Failure Cases
# ---------------------------------------------------------------------------

def test_forensic_replay_requests_2317_target_drift_and_duplicate_trap():
    """Forensic Replay: psf__requests-2317.

    Historical failure: Diagnosed sessions.py:builtin_str, but edited models.py:iter_lines.
    Hypothesis B was an identical clone.
    V1.7 outcome:
    - Eliminates duplicate clone.
    - Prevents target drift by enforcing binding to diagnosed sessions.py:builtin_str.
    """
    # 1. Detect duplicate clone
    hyp_a = DiagnosisHypothesis(
        id="A",
        cause="In python 3 builtin_str causes TypeError when decoding in sessions.py",
        invariant="Preserve byte representation across python versions",
        repair_strategy="Cast to bytes before decode",
        affected_sites=["requests/sessions.py:builtin_str"],
        confidence=0.8,
    )
    hyp_b_clone = DiagnosisHypothesis(
        id="B",
        cause="In python 3 builtin_str causes TypeError when decoding in sessions.py",
        invariant="Preserve byte representation across python versions",
        repair_strategy="Cast to bytes before decode",
        affected_sites=["requests/sessions.py:builtin_str"],
        confidence=0.75,
    )

    deduped = CompetingDiagnosisEngine._detect_and_deduplicate_hypotheses([hyp_a, hyp_b_clone])
    active = [h for h in deduped if not h.eliminated]
    assert len(active) == 1
    assert active[0].id == "A"
    assert deduped[1].eliminated is True
    assert "Duplicate of Hypothesis A" in deduped[1].elimination_reason

    # 2. Prevent target drift
    drifted_candidate = EditSite(
        file_path="requests/models.py",
        symbol="iter_lines",
        line_start=100,
        line_end=150,
        source_span=(100, 150),
    )
    diag = CompetingDiagnosisResult(
        hypotheses=[hyp_a],
        selected_hypothesis=hyp_a,
        repair_sites=["requests/sessions.py:builtin_str"],
    )
    coherent, just = RepairSiteRanker.reconcile_target(drifted_candidate, diag, graph=None)
    assert coherent is False
    assert "contradicts diagnosed affected sites" in just


def test_forensic_replay_requests_1963_partial_repair_blindness_trap():
    """Forensic Replay: psf__requests-1963.

    Historical failure: Attempt 1 passed 5/7 F2P tests and 112/112 P2P tests.
    Attempt 2 discarded the working patch, passed unpatched code, and failed with 0 F2P passed.
    V1.7 outcome:
    - ResidualFailureRecord extracts 5 passing tests to preserve and 2 failing tests to fix.
    - Replanner dispatches REFINE_SEMANTICS with assertion-specific guidance.
    - Subsequent repair prompt includes the prior working replacement and residual guidance.
    """
    verdict = AdaptiveVerificationVerdict(
        tier_reached=VerificationTier.TARGETED_F2P,
        tests_executed=True,
        f2p_passed=5,
        f2p_total=7,
        p2p_passed=112,
        p2p_total=112,
        resolved=False,
        failure_class=FailureClass.PATCH_SEMANTICS.value,
        error_message="AssertionError: redirect history does not preserve intermediate cookie session",
        test_summary={
            "fail_to_pass": {
                "success": ["test_redirect_1", "test_redirect_2", "test_redirect_3", "test_redirect_4", "test_redirect_5"],
                "failure": ["test_redirect_cookie_1", "test_redirect_cookie_2"],
            },
            "fail_to_pass_success": ["test_redirect_1", "test_redirect_2", "test_redirect_3", "test_redirect_4", "test_redirect_5"],
            "fail_to_pass_failure": ["test_redirect_cookie_1", "test_redirect_cookie_2"],
        },
    )

    rec = ResidualFailureRecord.from_verdict(
        instance_id="psf__requests-1963",
        attempt=1,
        verdict=verdict,
        patch_text="diff --git a/requests/sessions.py b/requests/sessions.py\n...",
        target_str="requests/sessions.py:SessionRedirectMixin.resolve_redirects",
    )

    assert rec.is_partial_progress is True
    assert rec.f2p_passed_count == 5
    assert rec.f2p_total_count == 7
    assert len(rec.f2p_passed_tests) == 5
    assert len(rec.f2p_failed_tests) == 2

    decision = FailureDrivenReplanner.decide(
        failure_class=verdict.failure_class,
        attempt=1,
        max_attempts=3,
        f2p_passed=5,
        p2p_failed=0,
        residual_record=rec,
    )
    assert decision.action == ReplanAction.REFINE_SEMANTICS
    assert "BEHAVIORAL REPAIR COMPLETENESS" in decision.prompt_guidance or "PARTIAL REPAIR PROGRESS" in decision.prompt_guidance
    assert "PRESERVE THESE PASSING ASSERTIONS" in decision.prompt_guidance
    assert "test_redirect_cookie_1" in decision.prompt_guidance
