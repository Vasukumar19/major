"""Structured trajectory recording for PatchForge v0.3."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from patchforge.tools.base import ToolCall, ToolResult


@dataclass
class TrajectoryStep:
    turn: int
    phase: str
    thought: str = ""
    action_name: str = ""
    action_args: dict[str, Any] = field(default_factory=dict)
    tool_result: dict[str, Any] = field(default_factory=dict)
    observation_summary: str = ""
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "turn": self.turn,
            "phase": self.phase,
            "thought": self.thought,
            "action_name": self.action_name,
            "action_args": self.action_args,
            "tool_result": self.tool_result,
            "observation_summary": self.observation_summary,
            "timestamp": self.timestamp,
        }


@dataclass
class AgentTrajectory:
    instance_id: str
    start_time: float = field(default_factory=time.time)
    end_time: float = 0.0
    steps: list[TrajectoryStep] = field(default_factory=list)
    final_verdict: str = "INCOMPLETE"
    resolved: bool = False

    def record_step(
        self,
        turn: int,
        phase: str,
        thought: str,
        tool_call: ToolCall | None,
        tool_result: ToolResult | None,
    ) -> None:
        step = TrajectoryStep(
            turn=turn,
            phase=phase,
            thought=thought,
            action_name=tool_call.name if tool_call else "none",
            action_args=tool_call.arguments if tool_call else {},
            tool_result=tool_result.to_dict() if tool_result else {},
            observation_summary=tool_result.summary() if tool_result else "",
        )
        self.steps.append(step)

    def finish(self, verdict: str, resolved: bool = False) -> None:
        self.end_time = time.time()
        self.final_verdict = verdict
        self.resolved = resolved

    def to_dict(self) -> dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "duration_seconds": round(self.end_time - self.start_time, 2) if self.end_time else 0.0,
            "final_verdict": self.final_verdict,
            "resolved": self.resolved,
            "total_steps": len(self.steps),
            "steps": [s.to_dict() for s in self.steps],
        }
