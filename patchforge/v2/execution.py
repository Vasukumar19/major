"""Candidate workspace lifecycle for V2 repair attempts."""
from __future__ import annotations

import hashlib
from pathlib import Path

from .candidates import Candidate, CandidateLedger, TestObservation
from .workspaces import OwnedWorkspace, WorkspaceManager


def digest_workspace(root: Path) -> str:
    """Stable digest of editable workspace content, excluding VCS/ownership files."""
    digest = hashlib.sha256()
    excluded = {".git", ".patchforge-workspace.json", ".patchforge_cache"}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or any(part in excluded for part in path.parts):
            continue
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


class CandidateWorkspaceService:
    """Creates a child workspace for every candidate and never mutates parents."""

    def __init__(self, manager: WorkspaceManager, ledger: CandidateLedger):
        self.manager = manager
        self.ledger = ledger
        self.workspaces: dict[str, OwnedWorkspace] = {}
        self.candidate_workspaces: dict[str, OwnedWorkspace] = {}

    def create_root(self, source: str | Path, base_revision: str) -> OwnedWorkspace:
        workspace = self.manager.create(source, base_revision=base_revision)
        self.workspaces[workspace.workspace_id] = workspace
        return workspace

    def create_candidate(
        self,
        parent: OwnedWorkspace,
        *,
        base_revision: str,
        patch_text: str,
        changed_contents: dict[str, str],
        rationale: str,
        parent_candidate_id: str | None = None,
    ) -> Candidate:
        workspace = self.manager.create(
            parent.root,
            base_revision=base_revision,
            parent_workspace_id=parent.workspace_id,
        )
        for relative_path, content in changed_contents.items():
            destination = self.manager.resolve_file(workspace, relative_path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(content, encoding="utf-8")
        candidate = Candidate.create(
            workspace_id=workspace.workspace_id,
            base_revision=base_revision,
            source_digest=digest_workspace(workspace.root),
            patch_text=patch_text,
            changed_files=list(changed_contents),
            rationale=rationale,
            parent_candidate_id=parent_candidate_id,
        )
        self.ledger.add(candidate)
        self.workspaces[workspace.workspace_id] = workspace
        self.candidate_workspaces[candidate.candidate_id] = workspace
        return candidate

    def observe(self, candidate: Candidate, observation: TestObservation) -> None:
        self.ledger.observe(candidate.candidate_id, observation)

    def workspace_for(self, candidate: Candidate) -> OwnedWorkspace:
        return self.candidate_workspaces[candidate.candidate_id]

    def register_existing_candidate(
        self,
        workspace: OwnedWorkspace,
        *,
        base_revision: str,
        patch_text: str,
        changed_files: list[str],
        rationale: str,
        parent_candidate_id: str | None = None,
    ) -> Candidate:
        """Record an already-mutated owned workspace (for tool-using agents).

        This is deliberately separate from ``create_candidate``: the caller
        must have obtained the workspace from this service, so an agent can
        never register an uncontrolled checkout as a candidate.
        """
        if workspace.workspace_id not in self.workspaces:
            raise ValueError("Candidate workspace is not owned by this service")
        candidate = Candidate.create(
            workspace_id=workspace.workspace_id,
            base_revision=base_revision,
            source_digest=digest_workspace(workspace.root),
            patch_text=patch_text,
            changed_files=changed_files,
            rationale=rationale,
            parent_candidate_id=parent_candidate_id,
        )
        self.ledger.add(candidate)
        self.candidate_workspaces[candidate.candidate_id] = workspace
        return candidate
