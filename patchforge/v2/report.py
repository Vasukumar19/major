"""V2RepairReport — structured, JSON-serialisable and Markdown trace of one repair run."""
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
    hypothesis_id: str = ""
    ranking_score: float = 0.0
    # Selected-test observation
    selected_test_output: str = ""
    selected_test_passed: list[str] = field(default_factory=list)
    selected_test_failed: list[str] = field(default_factory=list)
    selected_test_timed_out: bool = False
    failure_kind: str | None = None
    # Assertion-level failure evidence
    failure_evidence: list[dict[str, Any]] = field(default_factory=list)
    # Residual assertion guidance injected into next refinement
    residual_guidance: str = ""
    rejection_reason: str = ""


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
        ID of the candidate chosen by CandidateLedger.best_accepted() / ranking.
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
    competing_hypotheses: list[dict[str, Any]] = field(default_factory=list)

    # Candidate lineage & ledger
    candidates: list[CandidateRecord] = field(default_factory=list)

    # Replanning actions taken across attempts
    replanning_decisions: list[dict[str, Any]] = field(default_factory=list)

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

    def to_markdown_trace(self) -> str:
        """Produce an auditable, human-readable markdown trace of the repair run."""
        lines = [
            f"# PatchForge V2 Repair Trace — {self.instance_id}",
            "",
            "## 1. Run Metadata",
            f"- **Instance ID**: `{self.instance_id}`",
            f"- **Base Revision**: `{self.base_revision}`",
            f"- **Model**: `{self.model}`",
            f"- **Ollama URL**: `{self.ollama_url}`",
            f"- **Docker Image**: `{self.docker_image or '(none configured)'}`",
            f"- **Elapsed Time**: {self.elapsed_seconds}s",
            f"- **Resolved**: **{'YES' if self.resolved else 'NO'}**",
            f"- **F2P Improvement**: {'YES' if self.f2p_improvement else 'NO'}",
            f"- **Best Candidate ID**: `{self.best_candidate_id or '(none)'}`",
            "",
            "## 2. Diagnosis and Localization",
            f"- **Active Hypothesis**: {self.diagnosis_hypothesis or '(none)'}",
            f"- **Confidence**: {self.diagnosis_confidence}",
            f"- **Localized Files**: {', '.join(self.localized_files) or '(none)'}",
            "",
        ]

        if self.competing_hypotheses:
            lines.append("### Competing Hypotheses")
            for h in self.competing_hypotheses:
                lines.append(f"- **Hypothesis {h.get('id', '?')}** (conf={h.get('confidence', 0.0)}): {h.get('cause', '')} — {h.get('repair_strategy', '')}")
            lines.append("")

        lines.extend([
            "## 3. Candidate Lineage and Evidence Ledger",
            f"Total Candidates Generated: **{len(self.candidates)}**",
            "",
        ])

        for idx, cand in enumerate(self.candidates, start=1):
            parent = f"`{cand.parent_candidate_id}`" if cand.parent_candidate_id else "(root workspace)"
            lines.extend([
                f"### Candidate {idx}: `{cand.candidate_id}`",
                f"- **Parent**: {parent}",
                f"- **Status**: `{cand.status}`",
                f"- **Hypothesis**: `{cand.hypothesis_id or 'A'}`",
                f"- **Ranking Score**: {cand.ranking_score}",
                f"- **Rationale**: {cand.rationale}",
                f"- **Changed Files**: {', '.join(cand.changed_files) or '(none)'}",
            ])
            if cand.rejection_reason:
                lines.append(f"- **Rejection Reason**: {cand.rejection_reason}")
            if cand.selected_test_passed or cand.selected_test_failed:
                lines.append(f"- **Targeted Tests Passed**: {len(cand.selected_test_passed)} ({', '.join(cand.selected_test_passed[:3])})")
                lines.append(f"- **Targeted Tests Failed**: {len(cand.selected_test_failed)} ({', '.join(cand.selected_test_failed[:3])})")
            if cand.failure_kind:
                lines.append(f"- **Test Failure Kind**: `{cand.failure_kind}`")
            if cand.residual_guidance:
                lines.append(f"- **Residual Guidance Injected**:\n```\n{cand.residual_guidance[:400]}\n```")
            if cand.patch_text:
                lines.append(f"- **Diff**:\n```diff\n{cand.patch_text[:800]}\n```")
            lines.append("")

        lines.extend([
            "## 4. Final SWE-bench Verification Outcome",
            f"- **Official Resolved**: {self.resolved}",
            f"- **F2P Passed**: {self.harness_f2p_passed} / {self.harness_f2p_total}",
            f"- **P2P Passed**: {self.harness_p2p_passed} / {self.harness_p2p_total}",
            "",
        ])

        if self.notes:
            lines.append("## 5. Execution Notes")
            for note in self.notes:
                lines.append(f"- {note}")
            lines.append("")

        return "\n".join(lines)

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
