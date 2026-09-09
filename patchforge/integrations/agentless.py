"""Thin adapter over Agentless localization (subprocess CLI, unmodified upstream).

Proven Phase-B recipe baked in:
- structure JSON prebuilt from our own base_commit checkout (PROJECT_FILE_LOC),
  avoiding Agentless from-scratch clones;
- OpenRouter via OPENAI_API_KEY + OPENAI_BASE_URL (OpenAI-compatible);
- num_samples=1 (OpenRouter ignores batched n>1);
- diff assembly from original/new_file_content via difflib (upstream diff
  step needs a from-scratch playground checkout).
"""
from __future__ import annotations

import difflib
import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from patchforge.core.config import PatchForgeConfig
from patchforge.issue.problem import Problem

AGENTLESS_MODEL = "gpt-4o-mini-2024-07-18"


@dataclass
class AgentlessLocalization:
    instance_id: str = ""
    found_files: list[str] = field(default_factory=list)
    related_locs: dict = field(default_factory=dict)
    edit_locs: dict = field(default_factory=dict)
    usage: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "instance_id": self.instance_id,
            "found_files": self.found_files,
            "related_locs": self.related_locs,
            "edit_locs": self.edit_locs,
            "usage": self.usage,
        }


class AgentlessAdapter:
    def __init__(self, config: PatchForgeConfig | None = None, third_party: str = "third_party"):
        self.config = config or PatchForgeConfig()
        self.agentless = str((Path(third_party) / "Agentless").resolve())
        if not Path(self.agentless, "agentless", "fl", "localize.py").exists():
            raise RuntimeError(f"Agentless not found at {self.agentless}")

    def _env(self, structures_dir: str) -> dict:
        env = dict(os.environ)
        env["PYTHONPATH"] = self.agentless + os.pathsep + env.get("PYTHONPATH", "")
        env["PROJECT_FILE_LOC"] = structures_dir
        env["PYTHONUTF8"] = "1"
        if "OPENAI_BASE_URL" not in env:
            env["OPENAI_BASE_URL"] = self.config.openrouter_base_url
        return env

    def build_structure(self, instance_id: str, repo: str, base_commit: str, repo_dir: str, out_dir: str) -> str:
        """Prebuild the PROJECT_FILE_LOC JSON from our own checkout."""
        import sys
        sys.path.insert(0, self.agentless)
        try:
            from get_repo_structure.get_repo_structure import create_structure
            structure = create_structure(repo_dir)
        finally:
            sys.path.remove(self.agentless)
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        path = str(Path(out_dir) / f"{instance_id}.json")
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            json.dump({"repo": repo, "base_commit": base_commit,
                       "structure": structure, "instance_id": instance_id}, f)
        return path

    def _run(self, args: list[str], env: dict, timeout: int = 1200) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["python", str(Path(self.agentless, *args[0].split("/")))] + args[1:],
            capture_output=True, text=True, env=env, timeout=timeout,
        )

    def localize(
        self,
        problem: Problem,
        repo_dir: str,
        workdir: str,
        model: str = AGENTLESS_MODEL,
        top_n: int = 3,
    ) -> AgentlessLocalization:
        structs = str(Path(workdir) / "structures")
        self.build_structure(problem.instance_id, problem.repo, problem.base_commit, repo_dir, structs)
        env = self._env(structs)
        base = ["agentless/fl/localize.py", "--top_n", str(top_n),
                "--target_id", problem.instance_id,
                "--model", model, "--backend", "openai", "--num_threads", "1"]
        steps = [
            ("file_level",
             ["--file_level", "--output_folder", str(Path(workdir) / "file_level")]),
            ("related_elements",
             ["--related_level", "--compress",
              "--start_file", str(Path(workdir) / "file_level" / "loc_outputs.jsonl"),
              "--output_folder", str(Path(workdir) / "related_elements")]),
            ("edit_location_samples",
             ["--fine_grain_line_level", "--compress", "--temperature", "0.8",
              "--num_samples", "1",
              "--start_file", str(Path(workdir) / "related_elements" / "loc_outputs.jsonl"),
              "--output_folder", str(Path(workdir) / "edit_location_samples")]),
        ]
        out: dict[str, dict] = {}
        for key, extra in steps:
            proc = self._run(base[:1] + extra + base[1:], env)
            if proc.returncode != 0:
                raise RuntimeError(f"Agentless step {extra[0]} failed:\n{proc.stderr[-2000:]}")
            loc_file = str(Path(workdir) / key / "loc_outputs.jsonl")
            rows = [json.loads(line) for line in Path(loc_file).read_text(encoding="utf-8").splitlines()]
            out[key] = next(r for r in rows if r["instance_id"] == problem.instance_id)
        usage = {}
        for key, row in out.items():
            for traj in ("file_traj", "related_loc_traj", "edit_loc_traj"):
                items = row.get(traj)
                if isinstance(items, dict):
                    items = [items]
                for item in items or []:
                    u = (item or {}).get("usage")
                    if u:
                        usage.setdefault(key + ":" + traj, []).append(u)
        return AgentlessLocalization(
            instance_id=problem.instance_id,
            found_files=list(out["file_level"].get("found_files") or []),
            related_locs=out["related_elements"].get("found_related_locs") or {},
            edit_locs=out["edit_location_samples"].get("found_edit_locs") or {},
            usage=usage,
        )

    @staticmethod
    def assemble_diff(edited_files: list[str], original: list[str], new: list[str]) -> str:
        """Build a unified diff from Agentless original/new file contents."""
        parts = []
        for f, o, n in zip(edited_files, original, new):
            parts.append("".join(difflib.unified_diff(
                o.splitlines(True), n.splitlines(True),
                fromfile="a/" + f, tofile="b/" + f)))
        return "".join(parts)
