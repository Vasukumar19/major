"""V2 safety and candidate-lineage tests; no model or Docker service required."""
from __future__ import annotations

from pathlib import Path

import pytest

from patchforge.issue.problem import Problem
from patchforge.v2.candidates import CandidateLedger, CandidateStatus, TestObservation
from patchforge.v2.execution import CandidateWorkspaceService
from patchforge.v2.agent_runner import V2AgentRunner
from patchforge.v2.workspaces import WorkspaceManager, WorkspaceViolation


def test_candidate_child_preserves_parent_and_records_lineage(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    manager = WorkspaceManager(tmp_path / "owned")
    ledger = CandidateLedger(tmp_path / "artifacts" / "candidates.jsonl")
    service = CandidateWorkspaceService(manager, ledger)
    root = service.create_root(source, "base-1")

    first = service.create_candidate(
        root,
        base_revision="base-1",
        patch_text="diff first",
        changed_contents={"module.py": "VALUE = 2\n"},
        rationale="first repair",
    )
    ledger.accept(first.candidate_id)
    second = service.create_candidate(
        service.workspace_for(first),
        base_revision="base-1",
        patch_text="diff second",
        changed_contents={"module.py": "VALUE = 3\n"},
        rationale="residual repair",
        parent_candidate_id=first.candidate_id,
    )

    assert (root.root / "module.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert (service.workspace_for(first).root / "module.py").read_text(encoding="utf-8") == "VALUE = 2\n"
    assert (service.workspace_for(second).root / "module.py").read_text(encoding="utf-8") == "VALUE = 3\n"
    assert second.parent_candidate_id == first.candidate_id
    assert first.status is CandidateStatus.ACCEPTED
    assert ledger.artifact_path.read_text(encoding="utf-8").count("\n") == 3


def test_workspace_blocks_escape_and_unowned_deletion(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    manager = WorkspaceManager(tmp_path / "owned")
    workspace = manager.create(source, base_revision="base")
    with pytest.raises(WorkspaceViolation):
        manager.resolve_file(workspace, "../outside.py")

    unowned = manager.root / "unowned"
    unowned.mkdir()
    with pytest.raises(WorkspaceViolation):
        manager.destroy(type(workspace)("unowned", unowned, None, "base"))


def test_candidate_acceptance_selects_best_non_regressed(tmp_path: Path):
    ledger = CandidateLedger(tmp_path / "ledger.jsonl")
    from patchforge.v2.candidates import Candidate
    a = Candidate.create(workspace_id="a", base_revision="x", source_digest="a", patch_text="a", changed_files=[], rationale="a")
    b = Candidate.create(workspace_id="b", base_revision="x", source_digest="b", patch_text="b", changed_files=[], rationale="b")
    ledger.add(a)
    ledger.add(b)
    ledger.observe(a.candidate_id, TestObservation(phase="F2P", selectors=("a",), passed=("a",)))
    ledger.observe(b.candidate_id, TestObservation(phase="F2P", selectors=("b",), passed=("b", "c")))
    ledger.observe(b.candidate_id, TestObservation(phase="P2P", selectors=("p",), failed=("p",)))
    ledger.accept(a.candidate_id)
    ledger.accept(b.candidate_id)
    assert ledger.best_accepted() is a


def test_agent_runner_uses_owned_child_workspace_and_registers_patch(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    manager = WorkspaceManager(tmp_path / "owned")
    service = CandidateWorkspaceService(manager, CandidateLedger(tmp_path / "ledger.jsonl"))

    class Controller:
        received_tester = None
        received_repo = None
        def run(self, *, problem, repo_dir, tester):
            self.received_tester = tester
            self.received_repo = Path(repo_dir)
            (self.received_repo / "module.py").write_text("VALUE = 2\n", encoding="utf-8")
            from patchforge.repair.patch import Patch
            return Patch(valid=True, patch_text="diff", files_changed=["module.py"], reasoning="repair"), [], object()

    controller = Controller()
    result = V2AgentRunner(service, controller, tester="tester").run(
        Problem("id", "issue", "repo", "base"), source, "base"
    )
    assert result.candidate is not None
    assert controller.received_tester == "tester"
    assert controller.received_repo != result.root_workspace.root
    assert (source / "module.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert (service.workspace_for(result.candidate).root / "module.py").read_text(encoding="utf-8") == "VALUE = 2\n"
