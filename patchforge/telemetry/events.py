"""Telemetry and structured event logging for PatchForge AI."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class TelemetryEvent:
    task_id: str
    phase: str
    event_type: str
    timestamp: float = field(default_factory=time.time)
    duration_ms: float = 0.0
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "phase": self.phase,
            "event_type": self.event_type,
            "timestamp": round(self.timestamp, 3),
            "duration_ms": round(self.duration_ms, 2),
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": self.cost_usd,
            "details": self.details,
        }


class TelemetryLogger:
    """Records real-time execution events and computes accurate runtime breakdowns."""

    def __init__(self, task_id: str = ""):
        self.task_id = task_id
        self.events: list[TelemetryEvent] = []
        self.start_time = time.time()

    def log_event(
        self,
        phase: str,
        event_type: str,
        duration_ms: float = 0.0,
        model: str = "",
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_usd: float = 0.0,
        details: dict[str, Any] | None = None,
    ) -> TelemetryEvent:
        ev = TelemetryEvent(
            task_id=self.task_id,
            phase=phase,
            event_type=event_type,
            duration_ms=duration_ms,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
            details=details or {},
        )
        self.events.append(ev)
        return ev

    def get_breakdown(self) -> dict[str, Any]:
        total_wall_s = round(time.time() - self.start_time, 2)
        total_llm_ms = sum(e.duration_ms for e in self.events if "LLM" in e.event_type)
        total_test_ms = sum(e.duration_ms for e in self.events if "TEST" in e.event_type)
        total_tool_ms = sum(e.duration_ms for e in self.events if "TOOL" in e.event_type)
        total_input_toks = sum(e.input_tokens for e in self.events)
        total_output_toks = sum(e.output_tokens for e in self.events)

        return {
            "task_id": self.task_id,
            "total_wall_s": total_wall_s,
            "llm_time_s": round(total_llm_ms / 1000.0, 2),
            "test_time_s": round(total_test_ms / 1000.0, 2),
            "tool_time_s": round(total_tool_ms / 1000.0, 2),
            "total_input_tokens": total_input_toks,
            "total_output_tokens": total_output_toks,
            "total_tokens": total_input_toks + total_output_toks,
            "event_count": len(self.events),
        }
