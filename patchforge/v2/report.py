"""V2RepairReport — structured, JSON-serialisable record of one repair run.

Deliberately conservative: only fields that the V2 runner can populate
honestly are included. Callers must distinguish partial F2P improvement
from full task resolution.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class CandidateRecord:
    """Per-candidate evidence captured during the repair loop."""

    candidate_id: str
    parent_candidate_id: str | None
    base_revision: str
    patch_text: str
    changed_files: list[str]
    rationale: str
    status: str  # PENDING / ACCEPTED / REJECTED / FINAL
    # Selected-test observation (may be absent if executor unavailable)
    selected_test_output: str = ""
    selected_test_passed: list[str] = field(default_factory=list)
    selected_test_failed: list[str] = field(default_factory=list)
    selected_test_timed_out: bool = False
    # Residual assertion evidence injected into next refinement
    residual_guidance: str = ""


@dataclass
class V2RepairReport:
    """Complete, factual record of a V2 repair attempt.

    Resolution status
    -----------------
    ``resolved``:
        The official SWE-bench harness confirmed full F2P + P2P safety.
        Do NOT set this to True from partial F2P counts.
    ``f2p_improvement``:
        At least one F2P assertion was passed by the best candidate even
        if the task was not fully resolved.
    ``best_candidate_id``:
        ID of the candidate chosen by ``CandidateLedger.best_accepted()``.
    """

    instance_id: str
    base_revision: str
    model: str
    ollama_url: str
    docker_image: str
    started_at: float = field(default_factory=time.time)
    finished_at: float = 0.0

    # Diagnosis summary
    diagnosis_hypothesis: str = ""
    diagnosis_confidence: float = 0.0
    localized_files: list[str] = field(default_factory=list)

    # Candidate lineage
    candidates: list[CandidateRecord] = field(default_factory=list)

    # Outcome
    resolved: bool = False
    f2p_improvement: bool = False
    best_candidate_id: str | None = None
    harness_f2p_passed: int = 0
    harness_f2p_total: int = 0
    harness_p2p_passed: int = 0
    harness_p2p_total: int = 0
    harness_output: str = ""

    # Timing
    elapsed_seconds: float = 0.0

    # Free-form notes (errors, warnings, abort reasons)
    notes: list[str] = field(default_factory=list)

    # ----------------------------------------------------------------
    # Serialisation
    # ----------------------------------------------------------------

    def finish(self) -> None:
        """Mark the run as finished and compute elapsed time."""
        self.finished_at = time.time()
        self.elapsed_seconds = round(self.finished_at - self.started_at, 2)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def save(self, path: str | Path) -> None:
        """Write the report as pretty-printed JSON."""
        dest = Path(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with dest.open("w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, indent=2, sort_keys=True)

    @classmethod
    def load(cls, path: str | Path) -> "V2RepairReport":
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        candidates = [CandidateRecord(**c) for c in data.pop("candidates", [])]
        return cls(**data, candidates=candidates)

    # ----------------------------------------------------------------
    # Human-readable summary
    # ----------------------------------------------------------------

    def summary(self) -> str:
        lines = [
            f"V2 Repair Report — {self.instance_id}",
            f"  Model       : {self.model}",
            f"  Resolved    : {self.resolved}",
            f"  F2P improve : {self.f2p_improvement}",
            f"  Candidates  : {len(self.candidates)}",
        ]
        if self.best_candidate_id:
            lines.append(f"  Best cand.  : {self.best_candidate_id}")
        if self.resolved or self.harness_f2p_total > 0:
            lines.append(
                f"  Harness F2P : {self.harness_f2p_passed}/{self.harness_f2p_total}"
            )
            lines.append(
                f"  Harness P2P : {self.harness_p2p_passed}/{self.harness_p2p_total}"
            )
        lines.append(f"  Elapsed     : {self.elapsed_seconds}s")
        if self.notes:
            lines.append("  Notes:")
            for note in self.notes[-5:]:
                lines.append(f"    • {note}")
        return "\n".join(lines)
