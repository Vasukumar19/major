"""Ranked localization candidate with supporting evidence."""
from __future__ import annotations

from dataclasses import dataclass, field

from patchforge.retrieval.evidence import Evidence


@dataclass
class Candidate:
    file: str = ""
    symbol: str = ""
    score: float = 0.0
    evidence: list[Evidence] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "file": self.file,
            "symbol": self.symbol,
            "score": self.score,
            "evidence": [e.to_dict() for e in self.evidence],
        }
