"""Timing: aggregation only."""
from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class Timer:
    marks: dict = field(default_factory=dict)

    def start(self, name: str) -> None:
        self.marks[name] = time.time()

    def elapsed(self, name: str) -> float:
        return time.time() - self.marks.get(name, time.time())
