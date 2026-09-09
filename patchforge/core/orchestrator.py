"""End-to-end orchestration for one task.

SWE-bench -> checkout -> Agentless localization -> RepoGraph evidence ->
PatchForge hypotheses -> ranking -> hypothesis-tied patches ->
official-eval feedback loop -> episode artifacts.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from patchforge.core.config import PatchForgeConfig
from patchforge.core.state import RepairState
from patchforge.integrations.agentless import AgentlessAdapter
from patchforge.integrations.minisweagent import MiniSWEAgentAdapter
from patchforge.integrations.repograph import RepoGraphAdapter
from patchforge.integrations.swebench import SWEBenchAdapter
from patchforge.issue.interpreter import IssueInterpreter
from patchforge.localization.localizer import Localizer
from patchforge.memory.episode import Attempt, Episode, EpisodeStore
from patchforge.models.router import ModelRouter
from patchforge.reasoning.generator import HypothesisGenerator
from patchforge.reasoning.ranker import rank_hypotheses
from patchforge.repair.generator import PatchGenerator
from patchforge.repair.loop import RepairLoop
from patchforge.retrieval.retriever import Retriever
from patchforge.telemetry.cost import CostTracker
from patchforge.telemetry.timing import Timer
from patchforge.telemetry.tokens import TokenCounter

AGENTLESS_IN_RATE = 0.15 / 1_000_000
AGENTLESS_OUT_RATE = 0.60 / 1_000_000


class Orchestrator:
    def __init__(self, config: PatchForgeConfig | None = None, workspace: str = "."):
        self.config = config or PatchForgeConfig()
        self.workspace = workspace
        self.router = ModelRouter(role_models=dict(self.config.role_models),
                                  default=self.config.default_model)
        self.swebench = SWEBenchAdapter(self.config)
        self.agentless = AgentlessAdapter(self.config)
        self.repograph = RepoGraphAdapter(self.config)
        self.exec_env = MiniSWEAgentAdapter(self.config)
        self.timer = Timer()
        self.tokens = TokenCounter()
        self.cost = CostTracker()

    def _phase_dir(self, *parts: str) -> str:
        d = str(Path(self.workspace, *parts))
        Path(d).mkdir(parents=True, exist_ok=True)
        return d

    def _note(self, trajectory: list, stage: str, detail: str = "") -> None:
        trajectory.append({"t": round(time.time() - self._t0, 1),
                           "stage": stage, "detail": detail})

    def run_instance(self, instance_id: str) -> Episode:
        self._t0 = time.time()
        self.timer.start("task")
        deadline = self._t0 + self.config.max_task_seconds
        trajectory: list[dict] = []
        store = EpisodeStore(str(Path(self.workspace, "experiments", "phase_c")))
        task_dir = store.task_dir(instance_id)
        workdir = self._phase_dir("experiments", "phase_c", "work", instance_id)
        ep = Episode(instance_id=instance_id, model=self.config.default_model)

        def out_of_time() -> bool:
            return time.time() > deadline

        self._note(trajectory, "load_problem")
        problem = self.swebench.load_problem(instance_id)
        ep.problem = problem.to_dict()
        ep.repo, ep.base_commit = problem.repo, problem.base_commit
        IssueInterpreter().interpret(problem)
        store.save_artifact(task_dir, "problem.json", ep.problem)

        self._note(trajectory, "checkout")
        repo_dir = self.swebench.ensure_checkout(
            problem, self._phase_dir("experiments", "phase_c", "repos"))

        self._note(trajectory, "agentless_localize")
        aloc = self.agentless.localize(problem, repo_dir, workdir + "/agentless")
        agentless_tokens = self._sum_agentless_usage(workdir + "/agentless")
        self.tokens.input_tokens += agentless_tokens[0]
        self.tokens.output_tokens += agentless_tokens[1]
        self.cost.add_value(agentless_tokens[0] * AGENTLESS_IN_RATE
                            + agentless_tokens[1] * AGENTLESS_OUT_RATE)

        self._note(trajectory, "repograph")
        graph_pkl = self.repograph.ensure_graph(
            repo_dir, instance_id, self._phase_dir("experiments", "phase_c", "graphs"))

        self._note(trajectory, "localize")
        retriever = Retriever(repo_dir)
        localizer = Localizer(repograph=self.repograph, retriever=retriever)
        candidates = localizer.localize(problem, aloc, graph_pkl)
        ep.localization = {"top_candidates": [c.to_dict() for c in candidates]}
        ep.evidence = [e.to_dict() for c in candidates for e in c.evidence]
        store.save_artifact(task_dir, "evidence.json", ep.evidence)

        self._note(trajectory, "hypothesize")
        hgen = HypothesisGenerator(
            provider=self.router.provider_for("hypothesize"), retriever=retriever,
            max_hypotheses=self.config.max_hypotheses)
        hypotheses = hgen.generate(problem, candidates)
        hypotheses = rank_hypotheses(hypotheses, candidates)
        ep.hypotheses = [h.to_dict() for h in hypotheses]
        ep.selected_hypothesis = hypotheses[0].id if hypotheses else ""
        store.save_artifact(task_dir, "hypotheses.json", ep.hypotheses)

        self._note(trajectory, "repair_loop")
        pgen = PatchGenerator(provider=self.router.provider_for("repair"),
                              retriever=retriever)
        from patchforge.verification.tester import Tester
        tester = Tester(self.swebench, model_name="patchforge-" + self.config.default_model)
        loop = RepairLoop(self.config, pgen, tester,
                          exec_adapter=self.exec_env, repo_dir=repo_dir)
        state = RepairState(problem=problem, repo_dir=repo_dir, graph_pkl=graph_pkl,
                            candidates=candidates, hypotheses=hypotheses)
        loop_result = loop.run(problem, hypotheses, candidates,
                               workdir + "/repair", f"patchforge-{instance_id}")
        state.attempts = loop_result.attempts
        state.resolved = loop_result.resolved

        for attempt in loop_result.attempts:
            p = attempt.get("patch", {})
            toks = p.get("tokens", {})
            self.tokens.input_tokens += int(toks.get("input", 0) or 0)
            self.tokens.output_tokens += int(toks.get("output", 0) or 0)
            self.cost.add_value(float(p.get("cost_usd", 0.0) or 0.0))

        ep.attempts = [Attempt(hypothesis_id=a["hypothesis_id"], patch=a["patch"],
                               tests=a["tests"], failure_class=a["failure_class"],
                               runtime_s=a["runtime_s"]).__dict__
                       for a in loop_result.attempts]
        if loop_result.attempts:
            last = loop_result.attempts[-1]
            ep.final_patch = {
                "valid": last["patch"].get("valid", False),
                "files_changed": len(last["patch"].get("files_changed", [])),
                "hypothesis_id": last["hypothesis_id"],
            }
            ep.final_tests = (last["tests"].get("eval") or {})
            store.save_artifact(task_dir, "patch.diff",
                                last["patch"].get("patch_text", ""))
            store.save_artifact(task_dir, "tests.json",
                                [a["tests"] for a in loop_result.attempts])
        ep.status = "RESOLVED" if loop_result.resolved else (
            "TIMEOUT" if out_of_time() else "UNRESOLVED")
        ep.failure_stage = None if loop_result.resolved else loop_result.failure_class

        ep.input_tokens = self.tokens.input_tokens
        ep.output_tokens = self.tokens.output_tokens
        ep.cost_usd = round(self.cost.cost_usd, 6)
        ep.runtime_s = round(self.timer.elapsed("task"), 1)
        self._note(trajectory, "done", ep.status)
        store.save_artifact(task_dir, "trajectory.json", trajectory)
        store.save(ep, task_dir)
        return ep

    @staticmethod
    def _sum_agentless_usage(agentless_dir: str) -> tuple[int, int]:
        """Sum prompt/completion tokens from Agentless loc/repair rows."""
        prompt, completion = 0, 0

        def walk(obj):
            nonlocal prompt, completion
            if isinstance(obj, dict):
                if set(obj) >= {"prompt_tokens", "completion_tokens"}:
                    try:
                        prompt += int(obj.get("prompt_tokens") or 0)
                        completion += int(obj.get("completion_tokens") or 0)
                    except (TypeError, ValueError):
                        pass
                for v in obj.values():
                    walk(v)
            elif isinstance(obj, list):
                for v in obj:
                    walk(v)

        for path in Path(agentless_dir).rglob("*.jsonl"):
            try:
                for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                    try:
                        walk(json.loads(line))
                    except ValueError:
                        continue
            except OSError:
                continue
        return prompt, completion
