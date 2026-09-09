"""Hypothesis engine tests (Checkpoint 5). Fake provider, no network."""
import json

from patchforge.issue.problem import Problem
from patchforge.localization.candidate import Candidate
from patchforge.models.provider import Generation
from patchforge.reasoning.generator import (
    HypothesisGenerator,
    build_prompt,
    parse_hypotheses,
)
from patchforge.reasoning.ranker import rank_hypotheses
from patchforge.retrieval.evidence import Evidence


class FakeProvider:
    def __init__(self, texts):
        self.texts = texts
        self.calls = 0

    def generate_one(self, prompt, **kwargs):
        self.calls += 1
        return Generation(text=self.texts[min(self.calls - 1, len(self.texts) - 1)],
                          model="fake")

    def generate_many(self, prompt, n, **kwargs):
        return [self.generate_one(prompt, **kwargs) for _ in range(n)]


def _cands():
    return [
        Candidate(file="requests/models.py", symbol="register_hook", score=0.9,
                  evidence=[Evidence(source="agentless", type="SEMANTIC_MATCH",
                                     file="requests/models.py", symbol="register_hook",
                                     relevance=0.5),
                            Evidence(source="repograph", type="CALL_RELATION",
                                     file="requests/models.py", symbol="register_hook",
                                     relevance=0.8)]),
        Candidate(file="requests/sessions.py", symbol="request", score=0.8,
                  evidence=[Evidence(source="agentless", type="SEMANTIC_MATCH",
                                     file="requests/sessions.py", symbol="request",
                                     relevance=0.3)]),
    ]


def _problem():
    return Problem(instance_id="i", problem_statement="hooks broken",
                   fail_to_pass=["t1"])


def _payload():
    return json.dumps([
        {"id": "H1", "description": "register_hook mishandles lists",
         "affected_files": ["requests/models.py"], "affected_symbols": ["register_hook"],
         "confidence": 0.8, "expected_behavior": "lists accepted",
         "support_refs": [{"file": "requests/models.py", "symbol": "register_hook"}],
         "contradicting": ""},
        {"id": "H2", "description": "session drops hooks",
         "affected_files": ["requests/sessions.py"], "affected_symbols": ["request"],
         "confidence": 0.9, "expected_behavior": "hooks kept",
         "support_refs": [{"file": "requests/sessions.py", "symbol": "request"}],
         "contradicting": "no evidence of loss"},
    ])


def test_parse_tolerates_fences():
    assert len(parse_hypotheses("```json\n" + _payload() + "\n```")) == 2
    assert len(parse_hypotheses(_payload())) == 2


def test_prompt_contains_issue_candidates_tests():
    p = build_prompt(_problem(), _cands(), ["### code"])
    assert "hooks broken" in p and "register_hook" in p and "t1" in p


def test_generate_links_evidence_and_retries_on_garbage():
    gen = HypothesisGenerator(provider=FakeProvider(["not json", _payload()]))
    hs = gen.generate(_problem(), _cands())
    assert [h.id for h in hs] == ["H1", "H2"]
    assert hs[0].supporting_evidence[0].source == "repograph"
    assert hs[1].contradicting_evidence, "contradicting text preserved"
    assert gen.provider.calls == 2


def test_generate_caps_at_max():
    gen = HypothesisGenerator(provider=FakeProvider([_payload()]), max_hypotheses=1)
    assert len(gen.generate(_problem(), _cands())) == 1


def test_ranker_prefers_structural_support():
    gen = HypothesisGenerator(provider=FakeProvider([_payload()]))
    hs = rank_hypotheses(gen.generate(_problem(), _cands()), _cands())
    assert hs[0].id == "H1", hs[0].rank_breakdown
    assert hs[0].rank_breakdown["structural"] > 0
    assert set(hs[0].rank_breakdown) == {"semantic", "structural", "test", "error",
                                         "consistency", "total"}
