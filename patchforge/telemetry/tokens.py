"""Token accounting: aggregation only."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TokenCounter:
    input_tokens: int = 0
    output_tokens: int = 0

    def add(self, gen) -> None:
        self.input_tokens += int(getattr(gen, "input_tokens", 0) or 0)
        self.output_tokens += int(getattr(gen, "output_tokens", 0) or 0)

    def total(self) -> int:
        return self.input_tokens + self.output_tokens
