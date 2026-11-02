"""Owned, copy-on-write workspaces for V2 repair attempts."""
from __future__ import annotations

import json
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path


class WorkspaceViolation(ValueError):
    """Raised when an operation would escape an owned workspace."""


@dataclass(frozen=True)
class OwnedWorkspace:
    workspace_id: str
    root: Path
    parent_workspace_id: str | None
    base_revision: str

    @property
    def marker(self) -> Path:
        return self.root / ".patchforge-workspace.json"


class WorkspaceManager:
    """Creates and safely removes PatchForge-owned workspace copies.

    V2 deliberately copies an explicitly supplied base directory rather than
    resetting it. Git worktree optimisation may be added later behind this
    same interface; correctness does not depend on a particular VCS layout.
    """

    MARKER = ".patchforge-workspace.json"

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def create(
        self,
        source: str | Path,
        *,
        base_revision: str,
        parent_workspace_id: str | None = None,
        workspace_id: str | None = None,
    ) -> OwnedWorkspace:
        source_path = Path(source).resolve()
        if not source_path.is_dir():
            raise WorkspaceViolation(f"Source workspace does not exist: {source_path}")
        workspace_id = workspace_id or uuid.uuid4().hex
        if not workspace_id.replace("-", "").isalnum():
            raise WorkspaceViolation("Workspace identifier must be alphanumeric or hyphenated")
        destination = (self.root / workspace_id).resolve()
        self._ensure_contained(destination)
        if destination.exists():
            raise WorkspaceViolation(f"Workspace already exists: {workspace_id}")

        def ignore(directory: str, names: list[str]) -> set[str]:
            # Never copy an inherited PatchForge ownership marker or mutable
            # VCS metadata into an attempt workspace.
            return {name for name in names if name in {self.MARKER, ".patchforge_cache", ".git"}}

        shutil.copytree(source_path, destination, ignore=ignore)
        workspace = OwnedWorkspace(workspace_id, destination, parent_workspace_id, base_revision)
        workspace.marker.write_text(
            json.dumps(
                {
                    "workspace_id": workspace_id,
                    "parent_workspace_id": parent_workspace_id,
                    "base_revision": base_revision,
                    "owner": "patchforge-v2",
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        return workspace

    def resolve_file(self, workspace: OwnedWorkspace, relative_path: str) -> Path:
        if not relative_path or Path(relative_path).is_absolute():
            raise WorkspaceViolation("Workspace file path must be non-empty and relative")
        destination = (workspace.root / relative_path).resolve()
        try:
            destination.relative_to(workspace.root)
        except ValueError as exc:
            raise WorkspaceViolation(f"Path escapes workspace: {relative_path}") from exc
        return destination

    def destroy(self, workspace: OwnedWorkspace) -> None:
        self._ensure_contained(workspace.root)
        marker = workspace.marker
        if not marker.is_file():
            raise WorkspaceViolation(f"Refusing to delete unowned workspace: {workspace.root}")
        try:
            metadata = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise WorkspaceViolation("Refusing to delete unreadable ownership marker") from exc
        if metadata.get("owner") != "patchforge-v2" or metadata.get("workspace_id") != workspace.workspace_id:
            raise WorkspaceViolation("Refusing to delete workspace with mismatched ownership marker")
        shutil.rmtree(workspace.root)

    def _ensure_contained(self, path: Path) -> None:
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise WorkspaceViolation(f"Workspace path escapes manager root: {path}") from exc
