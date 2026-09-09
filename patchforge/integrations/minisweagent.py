"""Thin adapter over mini-SWE-agent execution (no custom sandbox).

PatchForge owns reasoning; mini-SWE-agent owns the environment.
Proven Phase-B recipe baked in:
- OpenRouterModel with capped max_tokens + cost_tracking=ignore_errors;
- LocalEnvironment whose commands run through WSL bash on Windows
  (plain LocalEnvironment uses CMD shell=True and cannot run the
  agent's bash; real repair runs target SWE-bench Linux Docker);
- submit via COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT convention.
"""
from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

from patchforge.core.config import PatchForgeConfig


@dataclass
class ExecResult:
    command: str = ""
    output: str = ""
    returncode: int = 0


class MiniSWEAgentAdapter:
    def __init__(self, config: PatchForgeConfig | None = None, third_party: str = "third_party"):
        self.config = config or PatchForgeConfig()
        self.mini = str((Path(third_party) / "mini-swe-agent" / "src").resolve())
        if not Path(self.mini, "minisweagent", "agents", "default.py").exists():
            raise RuntimeError(f"mini-SWE-agent not found at {self.mini}")
        import sys
        if self.mini not in sys.path:
            sys.path.insert(0, self.mini)
        self._patch_wsl_exec()

    @staticmethod
    def _to_wsl(path: str) -> str:
        p = path.replace("\\", "/")
        if len(p) >= 2 and p[1] == ":":
            return "/mnt/" + p[0].lower() + p[2:]
        return p

    def _patch_wsl_exec(self) -> None:
        """Route LocalEnvironment commands through WSL bash on Windows only."""
        import os
        if os.name == "posix":
            return
        import minisweagent.environments.local as local_mod

        def _wsl_run(command, cwd, env, timeout):
            inner = "cd " + shlex.quote(self._to_wsl(cwd)) + " && " + command
            process = subprocess.Popen(
                ["bash.exe", "-c", inner],
                text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                encoding="utf-8", errors="replace",
            )
            try:
                stdout, _ = process.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, _ = process.communicate()
                raise subprocess.TimeoutExpired(command, timeout, output=stdout)
            return subprocess.CompletedProcess(command, process.returncode, stdout=stdout)

        local_mod._run = _wsl_run

    def make_model(self, model: str | None = None, max_tokens: int = 2048):
        from minisweagent.models.openrouter_model import OpenRouterModel
        return OpenRouterModel(
            model_name=model or self.config.default_model,
            model_kwargs={"max_tokens": max_tokens},
            cost_tracking="ignore_errors",
        )

    def make_env(self, cwd: str):
        from minisweagent.environments.local import LocalEnvironment
        return LocalEnvironment(cwd=cwd, timeout=self.config.max_test_seconds)

    def run_bash(self, command: str, cwd: str, timeout: int | None = None) -> ExecResult:
        env = self.make_env(cwd)
        out = env.execute({"command": command}, cwd=cwd,
                          timeout=timeout or self.config.max_test_seconds)
        return ExecResult(command=command, output=out.get("output", ""),
                          returncode=int(out.get("returncode", -1)))

    def run_agent(self, task: str, repo_dir: str, model: str | None = None,
                  step_limit: int = 8, system_template: str = "") -> dict:
        from minisweagent.agents.default import DefaultAgent
        agent = DefaultAgent(
            self.make_model(model),
            self.make_env(repo_dir),
            system_template=system_template or (
                "You are a careful repair assistant. Inspect and edit the repository "
                "with bash commands. When finished, submit by running a command whose "
                "first output line is exactly COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT "
                "followed by your summary."),
            instance_template="{{task}}",
            step_limit=step_limit,
        )
        extra = agent.run(task)
        return {
            "exit_status": extra.get("exit_status", ""),
            "submission": str(extra.get("submission", "")),
            "n_messages": len(agent.messages),
            "model": model or self.config.default_model,
        }
