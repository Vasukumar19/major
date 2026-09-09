"""Localizer tests (Checkpoint 4). Deterministic; cached graph, no LLM."""
from patchforge.integrations.agentless import AgentlessLocalization
from patchforge.integrations.repograph import RepoGraphAdapter
from patchforge.issue.problem import Problem
from patchforge.localization.localizer import Localizer, parse_symbols
from patchforge.retrieval.retriever import Retriever

GRAPH = "experiments/baselines/02_repograph/psf__requests-863.graph.pkl"
REPO = "experiments/repos/requests"


def _localizer(**kw):
    return Localizer(repograph=RepoGraphAdapter(), retriever=Retriever(REPO), **kw)


def _loc():
    return AgentlessLocalization(
        instance_id="psf__requests-863",
        found_files=["requests/hooks.py", "requests/sessions.py", "requests/api.py",
                     "requests/__init__.py", "requests/models.py"],
        related_locs={"requests/sessions.py": ["function: __init__\nfunction: request"],
                      "requests/hooks.py": [""], "requests/api.py": [""]},
        edit_locs={"requests/sessions.py": ["function: Session.__init__\nline: 80"]},
    )


def _problem():
    return Problem(instance_id="psf__requests-863",
                   problem_statement="register_hook should accept a list of hooks",
                   fail_to_pass=["tests/test_requests.py::RequestsTestSuite::test_x"],
                   pass_to_pass=[])


def test_parse_symbols():
    assert parse_symbols(["function: __init__\nline: 80", "class: Foo"]) == ["__init__", "Foo"]
    assert parse_symbols([""]) == []
    assert parse_symbols("function: bar") == ["bar"]


def test_localize_returns_ranked_candidates_with_evidence():
    loc = _localizer(top_k=5).localize(_problem(), _loc(), GRAPH)
    assert 1 <= len(loc) <= 5
    scores = [c.score for c in loc]
    assert scores == sorted(scores, reverse=True)
    assert all(c.evidence for c in loc)
    assert ("requests/models.py", "register_hook") in [(c.file, c.symbol) for c in loc]


def test_graph_enrichment_attaches():
    loc = _localizer(top_k=10).localize(_problem(), _loc(), GRAPH)
    sym_cands = [c for c in loc if c.symbol]
    assert sym_cands
    assert any(e.source == "repograph" for c in sym_cands for e in c.evidence)


def test_deterministic():
    args = (_problem(), _loc(), GRAPH)
    a = [c.to_dict() for c in _localizer().localize(*args)]
    b = [c.to_dict() for c in _localizer().localize(*args)]
    assert a == b
