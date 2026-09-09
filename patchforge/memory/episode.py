"""Episode: the complete trace of one repair task. Storage only —
episodes never train or modify the system (no learning in Phase 1)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Attempt:
    hypothesis_id: str = ""
    patch: dict = field(default_factory=dict)
    tests: dict = field(default_factory=dict)
    failure_class: str = ""
    runtime_s: float = 0.0


@dataclass
class Episode:
    instance_id: str = ""
    repo: str = ""
    base_commit: str = ""
    model: str = ""
    problem: dict = field(default_factory=dict)
    localization: dict = field(default_factory=dict)
    evidence: list = field(default_factory=list)
    hypotheses: list = field(default_factory=list)
    selected_hypothesis: str = ""
    attempts: list = field(default_factory=list)
    final_patch: dict = field(default_factory=dict)
    final_tests: dict = field(default_factory=dict)
    status: str = "UNRESOLVED"
    failure_stage: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    runtime_s: float = 0.0

    def to_dict(self) -> dict:
        return {
            "instance_id": self.instance_id,
            "base_commit": self.base_commit,
            "status": self.status,
            "attempts": self.attempts,
            "localization": self.localization,
            "hypotheses": self.hypotheses,
            "selected_hypothesis": self.selected_hypothesis,
            "patch": self.final_patch,
            "tests": self.final_tests,
            "tokens": {"input": self.input_tokens, "output": self.output_tokens},
            "cost_usd": self.cost_usd,
            "runtime_seconds": self.runtime_s,
            "failure_stage": self.failure_stage,
        }


class EpisodeStore:
    def __init__(self, root: str):
        self.root = root

    def task_dir(self, instance_id: str) -> str:
        d = str(Path(self.root) / instance_id.replace("/", "-").replace(":", "-"))
        Path(d).mkdir(parents=True, exist_ok=True)
        return d

    def save(self, episode: Episode, task_dir: str) -> str:
        path = str(Path(task_dir) / "final_result.json")
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(episode.to_dict(), f, indent=2)
        return path

    def save_artifact(self, task_dir: str, name: str, payload) -> str:
        path = str(Path(task_dir) / name)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            if isinstance(payload, str):
                f.write(payload)
            else:
                json.dump(payload, f, indent=2)
        return path
