"""Evidence model tests (Checkpoint 3)."""
import pytest

from patchforge.retrieval.evidence import Evidence, EvidenceType
from patchforge.localization.candidate import Candidate


def test_evidence_round_trip():
    e = Evidence(
        source="repograph",
        type="CALL_RELATION",
        file="requests/models.py",
        symbol="register_hook",
        relevance=0.91,
        explanation="Request.prepare_request reaches register_hook",
    )
    assert Evidence.from_dict(e.to_dict()) == e


def test_relevance_clamped():
    assert Evidence(relevance=2.0).relevance == 1.0
    assert Evidence(relevance=-1.0).relevance == 0.0


def test_unknown_type_rejected():
    with pytest.raises(ValueError):
        Evidence(type="NOPE")


def test_all_spec_types_exist():
    for t in ["ISSUE_TEXT", "SYMBOL_MATCH", "SEMANTIC_MATCH", "GRAPH_RELATION",
              "IMPORT_RELATION", "CALL_RELATION", "TEST_REFERENCE", "STACK_TRACE",
              "ERROR_MESSAGE", "CODE_BEHAVIOR", "GIT_HISTORY"]:
        assert t in {x.value for x in EvidenceType}


def test_candidate_traceability():
    c = Candidate(file="requests/models.py", symbol="register_hook", score=0.94,
                  evidence=[Evidence(symbol="register_hook")])
    d = c.to_dict()
    assert d["evidence"][0]["symbol"] == "register_hook"
