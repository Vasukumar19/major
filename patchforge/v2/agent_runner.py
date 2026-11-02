"""Bridge the existing bounded AgentController into V2 workspace ownership."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from patchforge.issue.problem import Problem

from .candidates import Candidate
from .execution import CandidateWorkspaceService
from .workspaces import OwnedWorkspace


@dataclass
class V2AgentRunResult:
    candidate: Candidate | None
    root_workspace: OwnedWorkspace
    trajectory: Any
    state: Any


class V2AgentRunner:
    """Run a bounded tool agent only within a V2-owned child workspace.

    ``controller`` follows the existing AgentController.run contract. This
    bridge is intentionally narrow: it establishes ownership/lineage without
    importing V1 orchestration policy into V2.
    """

    def __init__(self, workspace_service: CandidateWorkspaceService, controller: Any, tester: Any = None):
        self.workspace_service = workspace_service
        self.controller = controller
        self.tester = tester

    def run(self, problem: Problem, source: str | Path, base_revision: str) -> V2AgentRunResult:
        root = self.workspace_service.create_root(source, base_revision)
        working = self.workspace_service.manager.create(
            root.root,
            base_revision=base_revision,
            parent_workspace_id=root.workspace_id,
        )
        self.workspace_service.workspaces[working.workspace_id] = working
        patch, trajectory, state = self.controller.run(
            problem=problem,
            repo_dir=str(working.root),
            tester=self.tester,
        )
        if not patch or not getattr(patch, "valid", False):
            return V2AgentRunResult(None, root, trajectory, state)
        candidate = self.workspace_service.register_existing_candidate(
            working,
            base_revision=base_revision,
            patch_text=getattr(patch, "patch_text", ""),
            changed_files=list(getattr(patch, "files_changed", []) or []),
            rationale=getattr(patch, "reasoning", "") or "tool-agent patch",
        )
        return V2AgentRunResult(candidate, root, trajectory, state)
