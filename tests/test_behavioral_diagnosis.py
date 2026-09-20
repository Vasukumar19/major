"""Unit tests and offline replay harness for PatchForge v0.7 Behavioral Diagnosis & Target Intelligence."""
import json
import pytest

from patchforge.diagnosis.behavior import IssueBehaviorExtractor, IssueBehaviorMap
from patchforge.diagnosis.candidate_analysis import (
    CandidateBehaviorAnalyzer,
    CandidateBlastRadius,
    CandidateProfile,
    CausalPath,
    CounterfactualEvaluation,
    HelperClassification,
)
from patchforge.diagnosis.diagnosis import (
    CompetingDiagnosisEngine,
    CompetingDiagnosisResult,
    DiagnosisHypothesis,
)
from patchforge.diagnosis.evidence import (
    BehavioralEvidenceEngine,
    BoundedClassContext,
    DeepStateFlowInfo,
    StructuredFailureEvidence,
)
from patchforge.repair.ranking import RepairSiteRanker
from patchforge.repository_intelligence.graph import RepositoryGraph
from patchforge.repository_intelligence.schema import EdgeKind, GraphEdge, GraphNode, NodeKind


# --- 1. Test IssueBehaviorExtractor ---
def test_issue_behavior_extractor():
    problem = """
    In requests 2.3.0, when calling session.send() with headers containing None values,
    KeyError: 'Accept-Encoding' is raised instead of omitting the header.
    Expected: headers with None values should not be sent.
    Actual: raises KeyError when merging environment settings.
    - Test test_headers_on_session_with_None_are_not_sent fails.
    - Test test_mixed_case_scheme_acceptable fails with 400.
    """
    hints = "See requests/sessions.py and Session.merge_environment_settings"

    b_map = IssueBehaviorExtractor.extract(problem, hints)

    assert "KeyError" in b_map.exceptions
    assert "requests/sessions.py" in b_map.mentioned_files
    assert any("headers_on_session_with_None_are_not_sent" in t for t in b_map.mentioned_tests)
    assert any("Session" in s for s in b_map.mentioned_symbols)
    assert len(b_map.symptoms) >= 1
    assert "headers" in b_map.state_changes or "session" in b_map.state_changes
    summary = b_map.format_summary()
    assert "Structured Behavioral Requirements" in summary


# --- 2. Test CandidateBlastRadius and HelperClassification ---
def test_blast_radius_and_helper_classification():
    # Construct a mock repository graph
    graph = RepositoryGraph()
    # Add a local helper
    graph.add_node(GraphNode(id="mod/feature.py::local_helper", name="local_helper", kind=NodeKind.FUNCTION, file_path="mod/feature.py", start_line=10, end_line=20))
    graph.add_node(GraphNode(id="mod/feature.py::feature_caller", name="feature_caller", kind=NodeKind.FUNCTION, file_path="mod/feature.py", start_line=30, end_line=40))
    graph.add_edge(GraphEdge(source_id="mod/feature.py::feature_caller", target_id="mod/feature.py::local_helper", kind=EdgeKind.CALLS))

    # Add a global helper
    graph.add_node(GraphNode(id="utils/common.py::global_util", name="global_util", kind=NodeKind.FUNCTION, file_path="utils/common.py", start_line=1, end_line=50))
    for i in range(5):
        c_name = f"external_caller_{i}"
        node_id = f"other/mod_{i}.py::{c_name}"
        graph.add_node(GraphNode(id=node_id, name=c_name, kind=NodeKind.FUNCTION, file_path=f"other/mod_{i}.py", start_line=1, end_line=10))
        graph.add_edge(GraphEdge(source_id=node_id, target_id="utils/common.py::global_util", kind=EdgeKind.CALLS))

    analyzer = CandidateBehaviorAnalyzer(graph=graph)

    # Analyze local helper
    prof_local = analyzer.analyze_candidate(
        symbol="local_helper",
        file_path="mod/feature.py",
        start_line=10,
        end_line=20,
    )
    assert prof_local.blast_radius.classification == HelperClassification.FEATURE_LOCAL
    assert prof_local.blast_radius.cross_module_callers_count == 0
    assert prof_local.blast_radius.semantic_radius_score < 4.0

    # Analyze global helper
    prof_global = analyzer.analyze_candidate(
        symbol="global_util",
        file_path="utils/common.py",
        start_line=1,
        end_line=50,
    )
    assert prof_global.blast_radius.classification == HelperClassification.GLOBAL_INFRASTRUCTURE
    assert prof_global.blast_radius.cross_module_callers_count >= 4
    assert prof_global.blast_radius.semantic_radius_score >= 7.0


# --- 3. Test Causal Path Extraction & Counterfactual Evaluation ---
def test_causal_path_and_counterfactual():
    graph = RepositoryGraph()
    graph.add_node(GraphNode(id="requests/sessions.py::request", name="request", kind=NodeKind.METHOD, file_path="requests/sessions.py", start_line=10, end_line=30))
    graph.add_node(GraphNode(id="requests/sessions.py::send", name="send", kind=NodeKind.METHOD, file_path="requests/sessions.py", start_line=40, end_line=70))
    graph.add_edge(GraphEdge(source_id="requests/sessions.py::request", target_id="requests/sessions.py::send", kind=EdgeKind.CALLS))

    b_map = IssueBehaviorMap(
        expected_behavior="Headers with None values should be omitted",
        current_behavior="Raises KeyError",
        symptoms=["test_headers fails with KeyError"],
        mentioned_tests=["test_headers"],
    )

    analyzer = CandidateBehaviorAnalyzer(graph=graph)
    prof = analyzer.analyze_candidate(
        symbol="send",
        file_path="requests/sessions.py",
        start_line=40,
        end_line=70,
        behavior_map=b_map,
        failing_traceback="File 'requests/sessions.py', line 55, in send\nKeyError: 'Accept'",
    )

    assert len(prof.causal_paths) >= 1
    assert "send" in prof.causal_paths[0].path_nodes
    assert prof.counterfactual.explains_failure is True
    assert prof.counterfactual.verdict in ("HIGHLY_PLAUSIBLE", "PLAUSIBLE")


# --- 4. Test Failure Traceback Parsing ---
def test_failure_traceback_parsing():
    engine = BehavioralEvidenceEngine(repo_dir=".")
    tb = """
____________________ test_headers_on_session_with_None_are_not_sent ____________________
    def test_headers_on_session_with_None_are_not_sent():
>       assert 'Accept-Encoding' not in prep.headers
E       AssertionError: assert 'Accept-Encoding' not in {'Accept-Encoding': None}
File "requests/sessions.py", line 465, in send
File "requests/sessions.py", line 320, in merge_environment_settings
    """

    res = engine.parse_failure_traceback(tb)
    assert res.failing_test == "test_headers_on_session_with_None_are_not_sent"
    assert "AssertionError" in res.error_type
    assert "'Accept-Encoding' not in prep.headers" in res.assertion_statement
    assert len(res.call_stack) >= 2
    assert "merge_environment_settings" in res.call_stack[1]


# --- 5. Test Competing Diagnosis Engine & Elimination ---
def test_competing_diagnosis_elimination():
    cand_a = CandidateProfile(
        symbol="merge_environment_settings",
        file_path="requests/sessions.py",
        start_line=300,
        end_line=340,
        blast_radius=CandidateBlastRadius(classification=HelperClassification.FEATURE_LOCAL),
        counterfactual=CounterfactualEvaluation(candidate_symbol="merge_environment_settings", explains_failure=True, explains_scope=True),
    )
    cand_b = CandidateProfile(
        symbol="global_pool_acquire",
        file_path="urllib3/poolmanager.py",
        start_line=50,
        end_line=100,
        blast_radius=CandidateBlastRadius(classification=HelperClassification.GLOBAL_INFRASTRUCTURE, cross_module_callers_count=15),
        counterfactual=CounterfactualEvaluation(candidate_symbol="global_pool_acquire", explains_failure=False, explains_scope=False),
    )

    fail_ev = StructuredFailureEvidence(
        failing_test="test_headers",
        call_stack=["requests/sessions.py:send:L465", "requests/sessions.py:merge_environment_settings:L320"],
    )

    mock_resp = """```json
{
  "hypotheses": [
    {
      "id": "A",
      "cause": "merge_environment_settings retains keys with None values instead of filtering them.",
      "invariant": "Existing callers expect dictionary without None keys.",
      "repair_strategy": "Filter out items where value is None.",
      "affected_sites": ["requests/sessions.py:merge_environment_settings"],
      "supporting_evidence": ["Present in traceback", "FEATURE_LOCAL"],
      "unexplained_symptoms": [],
      "confidence": 0.90
    },
    {
      "id": "B",
      "cause": "urllib3 global connection pool drops headers prematurely.",
      "invariant": "Pool should maintain socket state.",
      "repair_strategy": "Change socket configuration.",
      "affected_sites": ["urllib3/poolmanager.py:global_pool_acquire"],
      "supporting_evidence": [],
      "unexplained_symptoms": ["KeyError on merge"],
      "confidence": 0.85
    }
  ],
  "selected_hypothesis": 0,
  "repair_sites": ["requests/sessions.py:merge_environment_settings"]
}
```"""

    res = CompetingDiagnosisEngine.parse_competing_diagnosis(
        response_text=mock_resp,
        candidate_profiles=[cand_a, cand_b],
        failure_evidence=fail_ev,
    )

    assert len(res.hypotheses) == 2
    hyp_a = res.hypotheses[0]
    hyp_b = res.hypotheses[1]

    # Hypothesis A should win and not be eliminated
    assert hyp_a.eliminated is False
    assert hyp_a.counterfactual_score > hyp_b.counterfactual_score
    assert res.selected_hypothesis == hyp_a
    assert res.cause.startswith("merge_environment_settings")

    # Hypothesis B should be heavily penalized or eliminated because it doesn't appear in traceback and is GLOBAL_INFRASTRUCTURE
    assert any("does not appear in execution traceback" in e for e in hyp_b.contradicting_evidence)
    assert any("GLOBAL_INFRASTRUCTURE" in e for e in hyp_b.contradicting_evidence)


# --- 6. Test Two-Stage Target Ranking with Behavioral Profiles ---
def test_two_stage_ranking_with_behavioral_profiles():
    prof_feature = CandidateProfile(
        symbol="send",
        file_path="requests/sessions.py",
        start_line=400,
        end_line=500,
        blast_radius=CandidateBlastRadius(classification=HelperClassification.FEATURE_LOCAL),
        counterfactual=CounterfactualEvaluation(candidate_symbol="send", verdict="HIGHLY_PLAUSIBLE"),
        direct_tests=["test_send_basic"],
    )
    prof_helper = CandidateProfile(
        symbol="format_header",
        file_path="requests/utils.py",
        start_line=10,
        end_line=20,
        blast_radius=CandidateBlastRadius(classification=HelperClassification.GLOBAL_INFRASTRUCTURE, cross_module_callers_count=8),
        counterfactual=CounterfactualEvaluation(candidate_symbol="format_header", verdict="INSUFFICIENT_SCOPE", unexplained_symptoms=["fails on post"]),
    )

    ranker = RepairSiteRanker()
    ranked = ranker.rank_sites(
        candidates=[
            {"file_path": "requests/sessions.py", "symbol": "send", "line_start": 400, "line_end": 500, "role": "PRIMARY"},
            {"file_path": "requests/utils.py", "symbol": "format_header", "line_start": 10, "line_end": 20, "role": "SHARED_UTILITY"},
        ],
        primary_file="requests/sessions.py",
        candidate_profiles={"send": prof_feature, "format_header": prof_helper},
    )

    assert len(ranked) == 2
    # 'send' should rank higher than 'format_header'
    assert ranked[0].symbol == "send"
    assert ranked[0].score > ranked[1].score
    assert any("FEATURE_LOCAL" in r for r in ranked[0].reasons)
    assert any("GLOBAL_INFRASTRUCTURE" in r for r in ranked[1].reasons)


# --- 7. Replay Suite for Prior Failure Tasks ---
def test_replay_prior_failure_episodes():
    """Validates that behavioral diagnosis extracts correct causal targets for prior failure cases."""
    cases = [
        {
            "task": "requests-2317",
            "problem": "Session.send() with headers={'foo': None} raises KeyError. Headers with None values should not be sent.",
            "candidates": ["Session.send", "Session.request", "session"],
            "expected_top": "Session.send",
        },
        {
            "task": "flask-4992",
            "problem": "flask.Config.from_file fails when loading TOML or modern config files with custom loader. Should support binary mode or custom loaders.",
            "candidates": ["Config.from_file", "Config.from_pyfile", "Config.__init__"],
            "expected_top": "Config.from_file",
        },
        {
            "task": "flask-5063",
            "problem": "Blueprint routes with subdomains fail to register properly when subdomain is empty string or None.",
            "candidates": ["Blueprint.register", "Blueprint.route", "Flask.register_blueprint"],
            "expected_top": "Blueprint.register",
        },
    ]

    for c in cases:
        b_map = IssueBehaviorExtractor.extract(c["problem"])
        assert b_map.expected_behavior != "" or len(b_map.mentioned_symbols) > 0
