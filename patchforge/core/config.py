"""Central configuration. Phase 1: fixed values, no learning."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PatchForgeConfig:
    default_model: str = "openai/gpt-4o-mini"
    role_models: dict[str, str] = field(default_factory=dict)
    max_hypotheses: int = 3
    max_patch_attempts: int = 3
    max_task_seconds: int = 600
    max_test_seconds: int = 180
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    dataset_name: str = "SWE-bench/SWE-bench_Lite"
    workspace: str = "."
