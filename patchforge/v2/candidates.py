"""Immutable candidate lineage and test observations for V2."""
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


@dataclass
class Candidate:
    candidate_id: str
    workspace_id: str
    base_revision: str
    source_digest: str
    patch_digest: str
    changed_files: tuple[str, ...]
    rationale: str
    parent_candidate_id: str | None = None
    status: CandidateStatus = CandidateStatus.PENDING
    observations: list[TestObservation] = field(default_factory=list)
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
            parent_candidate_id=parent_candidate_id,
        )


class CandidateLedger:
    """Append-only JSONL ledger; no candidate may be silently overwritten."""

    def __init__(self, artifact_path: str | Path):
        self.artifact_path = Path(artifact_path)
        self.artifact_path.parent.mkdir(parents=True, exist_ok=True)
        self.candidates: dict[str, Candidate] = {}

    def add(self, candidate: Candidate) -> Candidate:
        if candidate.candidate_id in self.candidates:
            raise ValueError(f"Duplicate candidate id: {candidate.candidate_id}")
        if candidate.parent_candidate_id and candidate.parent_candidate_id not in self.candidates:
            raise ValueError("Candidate parent must already exist in this ledger")
        self.candidates[candidate.candidate_id] = candidate
        self._append("created", candidate)
        return candidate

    def observe(self, candidate_id: str, observation: TestObservation) -> None:
        candidate = self.candidates[candidate_id]
        if candidate.status is not CandidateStatus.PENDING:
            raise ValueError("Cannot add observations to a finalized candidate")
        candidate.observations.append(observation)
        self._append("observed", candidate, observation=asdict(observation))

    def accept(self, candidate_id: str, *, final: bool = False) -> None:
        candidate = self.candidates[candidate_id]
        if candidate.status is not CandidateStatus.PENDING:
            raise ValueError("Candidate already decided")
        candidate.status = CandidateStatus.FINAL if final else CandidateStatus.ACCEPTED
        self._append("accepted", candidate)

    def reject(self, candidate_id: str, reason: str) -> None:
        candidate = self.candidates[candidate_id]
        if candidate.status is not CandidateStatus.PENDING:
            raise ValueError("Candidate already decided")
        candidate.status = CandidateStatus.REJECTED
        self._append("rejected", candidate, reason=reason)

    def best_accepted(self) -> Candidate | None:
        accepted = [c for c in self.candidates.values() if c.status in {CandidateStatus.ACCEPTED, CandidateStatus.FINAL}]
        if not accepted:
            return None
        non_regressed = [c for c in accepted if self._p2p_failures(c) == 0]
        pool = non_regressed or accepted
        return max(pool, key=lambda c: (self._f2p_score(c), c.created_at))

    @staticmethod
    def _f2p_score(candidate: Candidate) -> int:
        return sum(len(o.passed) for o in candidate.observations if o.phase == "F2P")

    @staticmethod
    def _p2p_failures(candidate: Candidate) -> int:
        return sum(len(o.failed) for o in candidate.observations if o.phase == "P2P")

    def _append(self, event: str, candidate: Candidate, **extra: Any) -> None:
        record = {"event": event, "candidate": asdict(candidate), **extra}
        with self.artifact_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
