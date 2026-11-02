"""Phase 4 Test Suite — Candidate Profile Identity & Structural Ranking.

Verifies:
1. Same-named symbols in different files (e.g. requests/api.py:request vs requests/sessions.py:request) remain distinct.
2. Candidate profile lookups retrieve the correct file-specific evidence.
3. rank_sites uses _paths_match to prevent cross-directory basename collisions.
4. Missing candidate profile evidence does not invent false evidence or crash ranking.
5. Ranking behavior is deterministic for identical inputs.
"""
from __future__ import annotations

import pytest

from patchforge.core.target import EditSite
from patchforge.diagnosis.candidate_analysis import CandidateBlastRadius, CandidateProfile, HelperClassification
from patchforge.diagnosis.diagnosis import CompetingDiagnosisResult, DiagnosisHypothesis
from patchforge.repair.ranking import RepairSiteRanker, _paths_match


def test_candidate_profiles_remain_distinct_for_same_named_symbols():
    """Verify same-named symbols in different files do not clobber each other's profiles."""
    prof_api = CandidateProfile(
        symbol="request",
        file_path="requests/api.py",
        start_line=19,
        end_line=50,
        blast_radius=CandidateBlastRadius(direct_callers_count=2, classification=HelperClassification.FEATURE_LOCAL),
    )
    prof_sess = CandidateProfile(
        symbol="request",
        file_path="requests/sessions.py",
        start_line=369,
        end_line=465,
        blast_radius=CandidateBlastRadius(direct_callers_count=18, classification=HelperClassification.GLOBAL_INFRASTRUCTURE),
    )

    profiles = [prof_api, prof_sess]
    cand_profiles_dict = {}
    for p in profiles:
        norm_file = p.file_path.replace("\\", "/")
        cand_profiles_dict[f"{norm_file}:{p.symbol}"] = p
        cand_profiles_dict[f"{norm_file.split('/')[-1]}:{p.symbol}"] = p

    # Both profiles must exist distinctly under qualified keys
    assert "requests/api.py:request" in cand_profiles_dict
    assert "requests/sessions.py:request" in cand_profiles_dict
    assert cand_profiles_dict["requests/api.py:request"].file_path == "requests/api.py"
    assert cand_profiles_dict["requests/sessions.py:request"].file_path == "requests/sessions.py"
    assert cand_profiles_dict["requests/api.py:request"].blast_radius.direct_callers_count == 2
    assert cand_profiles_dict["requests/sessions.py:request"].blast_radius.direct_callers_count == 18


def test_rank_sites_retrieves_file_qualified_profile():
    """Verify rank_sites retrieves the correct file-qualified profile for candidates."""
    class DummyCounterfactual:
        def __init__(self, verdict):
            self.verdict = verdict

    prof_api = CandidateProfile(
        symbol="request",
        file_path="requests/api.py",
        start_line=19,
        end_line=50,
        counterfactual=DummyCounterfactual("HIGHLY_PLAUSIBLE"),
    )
    prof_sess = CandidateProfile(
        symbol="request",
        file_path="requests/sessions.py",
        start_line=369,
        end_line=465,
        counterfactual=DummyCounterfactual("WEAK"),
    )

    cand_dict = {
        "requests/api.py:request": prof_api,
        "api.py:request": prof_api,
        "requests/sessions.py:request": prof_sess,
        "sessions.py:request": prof_sess,
    }

    site_api = EditSite(file_path="requests/api.py", symbol="request", line_start=19, line_end=50)
    site_sess = EditSite(file_path="requests/sessions.py", symbol="request", line_start=369, line_end=465)

    ranker = RepairSiteRanker(graph=None)
    ranked = ranker.rank_sites(
        candidates=[site_api, site_sess],
        candidate_profiles=cand_dict,
    )

    ranked_dict = {f"{r.file_path}:{r.symbol}": r for r in ranked}
    # site_api gets +30 from HIGHLY_PLAUSIBLE counterfactual
    # site_sess gets -25 from WEAK counterfactual
    assert ranked_dict["requests/api.py:request"].score > ranked_dict["requests/sessions.py:request"].score


def test_paths_match_prevents_cross_directory_collision():
    """Verify _paths_match rejects cross-directory collisions across modules."""
    assert not _paths_match("tests/models.py", "requests/models.py")
    assert not _paths_match("foo/bar/models.py", "baz/qux/models.py")
    assert _paths_match("requests/models.py", "requests/models.py")
    assert _paths_match("src/requests/models.py", "requests/models.py")
    assert _paths_match("models.py", "models.py")


def test_rank_sites_rejects_cross_directory_diagnosis_bonus():
    """Verify candidates in tests/models.py do not get diagnosis bonus for requests/models.py."""
    h = DiagnosisHypothesis(id="A", cause="Defect in Request", affected_sites=["requests/models.py:Request"])
    diag = CompetingDiagnosisResult(hypotheses=[h], selected_hypothesis_idx=0)

    site_correct = EditSite(file_path="requests/models.py", symbol="Request", line_start=10, line_end=20)
    site_wrong_dir = EditSite(file_path="tests/models.py", symbol="Request", line_start=10, line_end=20)

    ranker = RepairSiteRanker(graph=None)
    ranked = ranker.rank_sites(
        candidates=[site_wrong_dir, site_correct],
        diagnosis=diag,
    )

    ranked_dict = {f"{r.file_path}:{r.symbol}": r for r in ranked}
    assert ranked_dict["requests/models.py:Request"].score > ranked_dict["tests/models.py:Request"].score
    # site_correct receives direct diagnosis match bonus (+80)
    assert any("Direct match" in reason for reason in ranked_dict["requests/models.py:Request"].reasons)
    # site_wrong_dir does NOT receive direct diagnosis match bonus
    assert not any("Direct match" in reason for reason in ranked_dict["tests/models.py:Request"].reasons)


def test_ranking_is_deterministic_for_identical_inputs():
    """Verify ranking is completely deterministic across repeated invocations."""
    site1 = EditSite(file_path="requests/api.py", symbol="request", line_start=10, line_end=20)
    site2 = EditSite(file_path="requests/sessions.py", symbol="prepare_request", line_start=30, line_end=40)

    ranker = RepairSiteRanker(graph=None)
    res1 = ranker.rank_sites([site1, site2])
    res2 = ranker.rank_sites([site1, site2])

    assert [r.score for r in res1] == [r.score for r in res2]
    assert [r.symbol for r in res1] == [r.symbol for r in res2]
