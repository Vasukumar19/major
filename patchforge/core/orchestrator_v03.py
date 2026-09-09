"""PatchForge v0.3 Agentic Orchestrator."""
from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

from patchforge.agent.context import ContextCompactor
from patchforge.agent.controller import AgentController
from patchforge.agent.policy import AgentPolicy
from patchforge.agent.state import AgentPhase, AgentState
from patchforge.agent.trajectory import AgentTrajectory
from patchforge.core.config import PatchForgeConfig
from patchforge.integrations.repograph import RepoGraphAdapter
from patchforge.integrations.swebench import SWEBenchAdapter
from patchforge.models.provider import OpenRouterProvider
from patchforge.models.router import ModelRouter
from patchforge.repair.patch import Patch
from patchforge.tools import create_default_tool_registry
from patchforge.verification.tester import Tester


class OrchestratorV03:
    """Orchestrates end-to-end autonomous debugging with PatchForge v0.3."""

    def __init__(self, config: PatchForgeConfig | None = None, workspace: str = "."):
        self.config = config or PatchForgeConfig()
        self.workspace = workspace
        self.swebench = SWEBenchAdapter(self.config)
        self.repograph = RepoGraphAdapter(self.config)
        self.router = ModelRouter(role_models=dict(self.config.role_models), default=self.config.default_model)

    def _phase_dir(self, *parts: str) -> str:
        d = str(Path(self.workspace, *parts))
        Path(d).mkdir(parents=True, exist_ok=True)
        return d

    def run_instance(
        self,
        instance_id: str,
        max_turns: int = 15,
        max_cost_usd: float = 1.0,
    ) -> tuple[Patch | None, AgentTrajectory, AgentState]:
        t0 = time.time()
        print(f"[{instance_id}] Starting PatchForge v0.3 Agentic Debugging...", flush=True)

        # 1. Load Problem
        problem = self.swebench.load_problem(instance_id)
        repo_base_dir = self._phase_dir("experiments", "phase_d_v03", "repos")
        repo_dir = self.swebench.ensure_checkout(problem, repo_base_dir)

        # 2. Setup Tester & Docker Integration
        tester = Tester(self.config, self.swebench)

        # 3. Setup Tool Registry & Agent Subsystems
        tools = create_default_tool_registry(
            repo_dir=repo_dir,
            repograph_adapter=self.repograph,
            tester=tester,
        )
        provider = self.router.provider_for("repair")
        policy = AgentPolicy(
            max_turns=max_turns,
            max_cost_usd=max_cost_usd,
            max_patch_attempts=self.config.max_patch_attempts,
        )
        compactor = ContextCompactor()

        controller = AgentController(
            provider=provider,
            tools=tools,
            policy=policy,
            compactor=compactor,
            model=self.config.default_model,
        )

        # 4. Run Agent Session
        best_patch, trajectory, state = controller.run(
            problem=problem,
            instance_id=instance_id,
            repo_dir=repo_dir,
            tester=tester,
            repograph_adapter=self.repograph,
        )

        # 5. Persist Trajectory & Artifacts
        exp_dir = self._phase_dir("experiments", "phase_d_v03", instance_id)
        with open(Path(exp_dir) / "trajectory.json", "w", encoding="utf-8") as f:
            json.dump(trajectory.to_dict(), f, indent=2)

        with open(Path(exp_dir) / "state.json", "w", encoding="utf-8") as f:
            json.dump(state.to_dict(), f, indent=2)

        print(
            f"[{instance_id}] Completed in {round(time.time() - t0, 1)}s. "
            f"Turns={state.turn_count}, Steps={len(trajectory.steps)}, "
            f"Score={state.best_score:.2f}, Cost=${state.total_cost_usd:.4f}",
            flush=True,
        )

        return best_patch, trajectory, state
