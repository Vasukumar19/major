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


@dataclass
class RepairEpisode:
    """Fine-grained episode record for structural repair data collection."""
    instance_id: str = ""
    repo: str = ""
    base_commit: str = ""
    model_name: str = ""
    target: dict = field(default_factory=dict)
    graph_context: dict = field(default_factory=dict)
    diagnosis: dict = field(default_factory=dict)  # cause, invariant, repair_strategy
    causal_chain: list[str] = field(default_factory=list)
    state_flow_summary: str = ""
    resolved: bool = False
    patch_applied: bool = False
    patch_valid: bool = False
    failure_class: str = ""
    runtime_s: float = 0.0
    f2p_passed: int = 0
    f2p_total: int = 0
    p2p_passed: int = 0
    p2p_total: int = 0
    refinement_cycles: int = 0
    refinement_history: list[dict] = field(default_factory=list)
    patch_text: str = ""
    test_evidence: dict = field(default_factory=dict)
    test_summary: dict = field(default_factory=dict)
    created_at: str = ""

    def to_dict(self) -> dict:
        return {
            "instance_id": self.instance_id,
            "repo": self.repo,
            "base_commit": self.base_commit,
            "model_name": self.model_name,
            "target": self.target,
            "graph_context": self.graph_context,
            "diagnosis": self.diagnosis,
            "causal_chain": self.causal_chain,
            "state_flow_summary": self.state_flow_summary,
            "resolved": self.resolved,
            "patch_applied": self.patch_applied,
            "patch_valid": self.patch_valid,
            "failure_class": self.failure_class,
            "runtime_s": self.runtime_s,
            "f2p_passed": self.f2p_passed,
            "f2p_total": self.f2p_total,
            "p2p_passed": self.p2p_passed,
            "p2p_total": self.p2p_total,
            "refinement_cycles": self.refinement_cycles,
            "refinement_history": self.refinement_history,
            "patch_text": self.patch_text,
            "test_evidence": self.test_evidence,
            "test_summary": self.test_summary,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> RepairEpisode:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})



def save_repair_episode(episode: RepairEpisode, directory: str = "results/episodes") -> str:
    """Saves a RepairEpisode to JSON storage."""
    p = Path(directory)
    p.mkdir(parents=True, exist_ok=True)
    file_path = p / f"{episode.instance_id}.json"
    file_path.write_text(json.dumps(episode.to_dict(), indent=2), encoding="utf-8")
    return str(file_path)
