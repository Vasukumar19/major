"""Transparent deterministic hypothesis ranking. No training (Phase 1).

score = semantic + structural + test + error + consistency, where:
- semantic:     mean relevance of SEMANTIC/SYMBOL/ISSUE supporting evidence
- structural:   0.2 per GRAPH/CALL/IMPORT evidence, capped at 0.6
- test:         0.3 if any TEST_REFERENCE support
- error:        0.3 per ERROR/STACK/CODE_BEHAVIOR-from-execution support
                (0 before any test has run)
- consistency:  fraction of affected files present in localization candidates
Model confidence is recorded but NOT part of the score (kept separate
so ranking stays auditable).
"""
from __future__ import annotations

from patchforge.localization.candidate import Candidate
from patchforge.reasoning.hypothesis import Hypothesis

_STRUCTURAL = {"GRAPH_RELATION", "CALL_RELATION", "IMPORT_RELATION"}
_SEMANTIC = {"SEMANTIC_MATCH", "SYMBOL_MATCH", "ISSUE_TEXT"} | _STRUCTURAL
_ERROR = {"ERROR_MESSAGE", "STACK_TRACE", "CODE_BEHAVIOR"}


def score_hypothesis(h: Hypothesis, candidates: list[Candidate]) -> dict:
    sem = [e.relevance for e in h.supporting_evidence if e.type in _SEMANTIC]
    semantic = sum(sem) / len(sem) if sem else 0.0
    structural = min(0.6, 0.2 * sum(1 for e in h.supporting_evidence if e.type in _STRUCTURAL))
    test = 0.3 if any(e.type == "TEST_REFERENCE" for e in h.supporting_evidence) else 0.0
    error = min(0.6, 0.3 * sum(1 for e in h.supporting_evidence if e.type in _ERROR
                               and e.source in ("execution", "test")))
    cand_files = {c.file for c in candidates}
    consistency = (len(set(h.affected_files) & cand_files) / len(h.affected_files)
                   if h.affected_files else 0.0)
    total = semantic + structural + test + error + consistency
    return {"semantic": round(semantic, 3), "structural": round(structural, 3),
            "test": test, "error": error, "consistency": round(consistency, 3),
            "total": round(total, 3)}


def rank_hypotheses(hypotheses: list[Hypothesis], candidates: list[Candidate]) -> list[Hypothesis]:
    for h in hypotheses:
        h.rank_breakdown = score_hypothesis(h, candidates)
        h.rank_score = h.rank_breakdown["total"]
    return sorted(hypotheses, key=lambda h: (-h.rank_score, -h.confidence, h.id))
