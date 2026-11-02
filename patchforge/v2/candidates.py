"""Immutable candidate lineage, evidence-based ranking, and test observations for V2."""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class CandidateStatus(str, Enum):
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    FINAL = "FINAL"


@dataclass(frozen=True)
class TestObservation:
    __test__ = False
    phase: str
    selectors: tuple[str, ...]
    passed: tuple[str, ...] = ()
    failed: tuple[str, ...] = ()
    output: str = ""
    exit_code: int | None = None
    timed_out: bool = False
    failure_kind: str | None = None  # None, "TIMEOUT", "SETUP_FAILURE", "TEST_FAILURE", "EXECUTOR_FAILURE"


@dataclass
class Candidate:
    candidate_id: str
    workspace_id: str
    base_revision: str
    source_digest: str
    patch_digest: str
    changed_files: tuple[str, ...]
    rationale: str
    patch_text: str = ""
    hypothesis_id: str = ""
    model_metadata: dict[str, Any] = field(default_factory=dict)
    parent_candidate_id: str | None = None
    status: CandidateStatus = CandidateStatus.PENDING
    observations: list[TestObservation] = field(default_factory=list)
    verification_verdict: dict[str, Any] = field(default_factory=dict)
    failure_evidence: list[dict[str, Any]] = field(default_factory=list)
    rejection_reason: str = ""
    ranking_score: float = 0.0
    created_at: float = field(default_factory=time.time)

    @classmethod
    def create(
        cls,
        *,
        workspace_id: str,
        base_revision: str,
        source_digest: str,
        patch_text: str,
        changed_files: list[str],
        rationale: str,
        hypothesis_id: str = "",
        model_metadata: dict[str, Any] | None = None,
        parent_candidate_id: str | None = None,
    ) -> "Candidate":
        return cls(
            candidate_id=uuid.uuid4().hex,
            workspace_id=workspace_id,
            base_revision=base_revision,
            source_digest=source_digest,
            patch_digest=hashlib.sha256(patch_text.encode("utf-8")).hexdigest(),
            changed_files=tuple(sorted(set(changed_files))),
            rationale=rationale,
            patch_text=patch_text,
            hypothesis_id=hypothesis_id,
            model_metadata=model_metadata or {},
            parent_candidate_id=parent_candidate_id,
        )


def compute_candidate_score(candidate: Candidate) -> float:
    """Compute multi-attribute ranking score for a candidate.

    Guarantees:
    - Candidates with regressions receive a severe penalty (-500/regression).
    - Resolved candidates receive top-tier bonus (+1000).
    - F2P progress is rewarded (+50/passed).
    - Smaller, focused patches break ties over bloated patches.
    """
    score = 0.0

    # 1. Full harness resolution
    if candidate.verification_verdict.get("resolved") or candidate.status is CandidateStatus.FINAL:
        score += 1000.0

    # 2. Test observations
    f2p_passed = sum(len(o.passed) for o in candidate.observations if o.phase == "F2P")
    f2p_failed = sum(len(o.failed) for o in candidate.observations if o.phase == "F2P")
    p2p_failed = sum(len(o.failed) for o in candidate.observations if o.phase == "P2P")

    # Harness verdict numbers if available
    harness_f2p = int(candidate.verification_verdict.get("f2p_passed", 0) or 0)
    harness_p2p_failed = int(candidate.verification_verdict.get("p2p_failed", 0) or 0)
    effective_f2p = max(f2p_passed, harness_f2p)
    effective_regressions = max(p2p_failed, harness_p2p_failed)

    score += effective_f2p * 50.0
    score -= effective_regressions * 500.0  # Crucial guard against regression

    if effective_f2p > 0 and effective_regressions == 0:
        score += 20.0  # Clean progress bonus

    # 3. Patch scope and size penalty
    files_count = len(candidate.changed_files)
    score -= files_count * 2.0
    diff_lines = len(candidate.patch_text.splitlines()) if candidate.patch_text else 0
    score -= diff_lines * 0.01

    # 4. Status adjustment
    if candidate.status is CandidateStatus.REJECTED:
        score -= 100.0

    # 5. Model confidence if present
    confidence = float(candidate.model_metadata.get("confidence", 0.0) or 0.0)
    score += confidence * 10.0

    return round(score, 2)


class CandidateLedger:
    """Append-only JSONL ledger; no candidate may be silently overwritten."""

    def __init__(self, artifact_path: str | Path):
        self.artifact_path = Path(artifact_path)
        self.artifact_path.parent.mkdir(parents=True, exist_ok=True)
        self.candidates: dict[str, Candidate] = {}

    def get_candidate(self, candidate_id: str) -> Candidate | None:
        return self.candidates.get(candidate_id)

    def add(self, candidate: Candidate) -> Candidate:
        if candidate.candidate_id in self.candidates:
            raise ValueError(f"Duplicate candidate id: {candidate.candidate_id}")
        if candidate.parent_candidate_id and candidate.parent_candidate_id not in self.candidates:
            raise ValueError("Candidate parent must already exist in this ledger")
        candidate.ranking_score = compute_candidate_score(candidate)
        self.candidates[candidate.candidate_id] = candidate
        self._append("created", candidate)
        return candidate

    def observe(self, candidate_id: str, observation: TestObservation) -> None:
        candidate = self.candidates[candidate_id]
        if candidate.status is not CandidateStatus.PENDING:
            raise ValueError("Cannot add observations to a finalized candidate")
        candidate.observations.append(observation)
        candidate.ranking_score = compute_candidate_score(candidate)
        self._append("observed", candidate, observation=asdict(observation))

    def record_verdict(self, candidate_id: str, verdict: dict[str, Any]) -> None:
        candidate = self.candidates[candidate_id]
        candidate.verification_verdict = dict(verdict)
        candidate.ranking_score = compute_candidate_score(candidate)
        self._append("verdict", candidate, verdict=verdict)

    def record_evidence(self, candidate_id: str, evidence: list[dict[str, Any]]) -> None:
        candidate = self.candidates[candidate_id]
        candidate.failure_evidence = list(evidence)
        self._append("evidence", candidate, failure_evidence=evidence)

    def accept(self, candidate_id: str, *, final: bool = False) -> None:
        candidate = self.candidates[candidate_id]
        if candidate.status is not CandidateStatus.PENDING:
            raise ValueError("Candidate already decided")
        candidate.status = CandidateStatus.FINAL if final else CandidateStatus.ACCEPTED
        candidate.ranking_score = compute_candidate_score(candidate)
        self._append("accepted", candidate)

    def reject(self, candidate_id: str, reason: str) -> None:
        candidate = self.candidates[candidate_id]
        if candidate.status is not CandidateStatus.PENDING:
            raise ValueError("Candidate already decided")
        candidate.status = CandidateStatus.REJECTED
        candidate.rejection_reason = reason
        candidate.ranking_score = compute_candidate_score(candidate)
        self._append("rejected", candidate, reason=reason)

    def rank_candidates(self) -> list[Candidate]:
        """Rank all candidates by multi-attribute evidence score."""
        for c in self.candidates.values():
            c.ranking_score = compute_candidate_score(c)
        return sorted(
            self.candidates.values(),
            key=lambda c: (
                c.ranking_score,
                1 if c.status in {CandidateStatus.ACCEPTED, CandidateStatus.FINAL} else 0,
                -self._p2p_failures(c),
                self._f2p_score(c),
                c.created_at,
            ),
            reverse=True,
        )

    def best_candidate(self) -> Candidate | None:
        ranked = self.rank_candidates()
        return ranked[0] if ranked else None

    def best_accepted(self) -> Candidate | None:
        accepted = [
            c for c in self.candidates.values()
            if c.status in {CandidateStatus.ACCEPTED, CandidateStatus.FINAL}
        ]
        if not accepted:
            return None
        # Non-regressed candidates take strict precedence
        non_regressed = [c for c in accepted if self._p2p_failures(c) == 0]
        pool = non_regressed or accepted
        return max(
            pool,
            key=lambda c: (
                compute_candidate_score(c),
                self._f2p_score(c),
                c.created_at,
            ),
        )

    @staticmethod
    def _f2p_score(candidate: Candidate) -> int:
        obs_passed = sum(len(o.passed) for o in candidate.observations if o.phase == "F2P")
        harness_passed = int(candidate.verification_verdict.get("f2p_passed", 0) or 0)
        return max(obs_passed, harness_passed)

    @staticmethod
    def _p2p_failures(candidate: Candidate) -> int:
        obs_reg = sum(len(o.failed) for o in candidate.observations if o.phase == "P2P")
        harness_reg = int(candidate.verification_verdict.get("p2p_failed", 0) or 0)
        return max(obs_reg, harness_reg)

    def _append(self, event: str, candidate: Candidate, **extra: Any) -> None:
        record = {"event": event, "candidate": asdict(candidate), **extra}
        with self.artifact_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
