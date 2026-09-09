"""Every localization/hypothesis decision must be traceable to evidence."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class EvidenceType(str, Enum):
    ISSUE_TEXT = "ISSUE_TEXT"
    SYMBOL_MATCH = "SYMBOL_MATCH"
    SEMANTIC_MATCH = "SEMANTIC_MATCH"
    GRAPH_RELATION = "GRAPH_RELATION"
    IMPORT_RELATION = "IMPORT_RELATION"
    CALL_RELATION = "CALL_RELATION"
    TEST_REFERENCE = "TEST_REFERENCE"
    STACK_TRACE = "STACK_TRACE"
    ERROR_MESSAGE = "ERROR_MESSAGE"
    CODE_BEHAVIOR = "CODE_BEHAVIOR"
    GIT_HISTORY = "GIT_HISTORY"
    STATIC_REFERENCE = "STATIC_REFERENCE"
    IDENTIFIER_MATCH = "IDENTIFIER_MATCH"


@dataclass
class Evidence:
    source: str = ""
    type: str = EvidenceType.ISSUE_TEXT.value
    file: str = ""
    symbol: str = ""
    line: int = 0
    relevance: float = 0.0
    explanation: str = ""

    def __post_init__(self):
        allowed = {t.value for t in EvidenceType}
        if self.type not in allowed:
            raise ValueError(f"Unknown evidence type: {self.type}")
        self.relevance = max(0.0, min(1.0, float(self.relevance)))

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "type": self.type,
            "file": self.file,
            "symbol": self.symbol,
            "line": self.line,
            "relevance": self.relevance,
            "explanation": self.explanation,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Evidence":
        return cls(
            source=d.get("source", ""),
            type=d.get("type", EvidenceType.ISSUE_TEXT.value),
            file=d.get("file", ""),
            symbol=d.get("symbol", ""),
            line=int(d.get("line", 0) or 0),
            relevance=float(d.get("relevance", 0.0) or 0.0),
            explanation=d.get("explanation", ""),
        )
