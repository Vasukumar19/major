"""Cost accounting: aggregation only."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CostTracker:
    cost_usd: float = 0.0

    def add(self, gen) -> None:
        self.cost_usd += float(getattr(gen, "cost_usd", 0.0) or 0.0)

    def add_value(self, value: float) -> None:
        self.cost_usd += value
