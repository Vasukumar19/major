"""Comprehensive tests for canonical V2 candidate ranking, evidence extraction, and runner."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from patchforge.repair.assertion_failures import AssertionFailure, AssertionFailureExtractor
from patchforge.repair.replanner import FailureDrivenReplanner, ReplanAction
from patchforge.repair.residual import ResidualFailureRecord
from patchforge.v2.candidates import (
    Candidate,
    CandidateLedger,
    CandidateStatus,
    TestObservation,
    compute_candidate_score,
)
from patchforge.v2.docker_executor import DockerSelectedTestExecutor
from patchforge.v2.execution import CandidateWorkspaceService
from patchforge.v2.report import CandidateRecord, V2RepairReport
from patchforge.v2.runner import V2RepairRunner, V2RunnerConfig
from patchforge.v2.workspaces import WorkspaceManager


# ---------------------------------------------------------------------------
# Test Candidate Preservation & Multi-Attribute Ranking
# ---------------------------------------------------------------------------

class TestCandidateRankingAndPreservation:
    def test_candidate_preserves_diff_and_metadata(self, tmp_path: Path):
        ledger = CandidateLedger(tmp_path / "candidates.jsonl")
        c1 = Candidate.create(
            workspace_id="ws1",
            base_revision="base1",
            source_digest="src1",
            patch_text="--- a/foo.py\n+++ b/foo.py\n+x = 1\n",
            changed_files=["foo.py"],
            rationale="initial fix",
            hypothesis_id="A",
            model_metadata={"tokens": 150},
        )
        ledger.add(c1)
        assert c1.patch_text.strip() == "--- a/foo.py\n+++ b/foo.py\n+x = 1"
        assert c1.hypothesis_id == "A"
        assert c1.model_metadata["tokens"] == 150
        assert c1.status is CandidateStatus.PENDING

    def test_regression_penalty_prevents_regressed_candidate_from_outranking_clean(self, tmp_path: Path):
        ledger = CandidateLedger(tmp_path / "candidates.jsonl")

        # Candidate 1: Fixed 2 F2P tests, but introduced 1 P2P regression
        c1 = Candidate.create(
            workspace_id="ws1",
            base_revision="base1",
            source_digest="src1",
            patch_text="+fix_all()",
            changed_files=["foo.py"],
            rationale="broad fix",
        )
        ledger.add(c1)
        ledger.observe(c1.candidate_id, TestObservation(phase="F2P", selectors=("t1", "t2"), passed=("t1", "t2")))
        ledger.observe(c1.candidate_id, TestObservation(phase="P2P", selectors=("p1",), failed=("p1",)))
        ledger.accept(c1.candidate_id)

        # Candidate 2: Fixed only 1 F2P test, but NO regressions
        c2 = Candidate.create(
            workspace_id="ws2",
            base_revision="base1",
            source_digest="src2",
            patch_text="+fix_one()",
            changed_files=["foo.py"],
            rationale="conservative fix",
        )
        ledger.add(c2)
        ledger.observe(c2.candidate_id, TestObservation(phase="F2P", selectors=("t1",), passed=("t1",)))
        ledger.observe(c2.candidate_id, TestObservation(phase="P2P", selectors=("p1",), passed=("p1",)))
        ledger.accept(c2.candidate_id)

        best = ledger.best_accepted()
        assert best is not None
        # Clean candidate c2 MUST be chosen over regressed c1!
        assert best.candidate_id == c2.candidate_id
        assert c2.ranking_score > c1.ranking_score

    def test_resolved_candidate_scores_highest(self, tmp_path: Path):
        ledger = CandidateLedger(tmp_path / "candidates.jsonl")
        c1 = Candidate.create(
            workspace_id="ws1",
            base_revision="base1",
            source_digest="src1",
            patch_text="+resolved()",
            changed_files=["foo.py"],
            rationale="perfect fix",
        )
        ledger.add(c1)
        ledger.record_verdict(c1.candidate_id, {"resolved": True, "f2p_passed": 3, "p2p_failed": 0})
        ledger.accept(c1.candidate_id, final=True)

        assert c1.ranking_score >= 1000.0
        assert ledger.best_accepted() == c1

    def test_smaller_patch_breaks_tie(self, tmp_path: Path):
        ledger = CandidateLedger(tmp_path / "candidates.jsonl")

        c1 = Candidate.create(
            workspace_id="ws1",
            base_revision="base1",
            source_digest="src1",
            patch_text="\n".join(["+line"] * 200),
            changed_files=["foo.py", "bar.py", "baz.py"],
            rationale="large diff",
        )
        ledger.add(c1)
        ledger.observe(c1.candidate_id, TestObservation(phase="F2P", selectors=("t1",), passed=("t1",)))
        ledger.accept(c1.candidate_id)

        c2 = Candidate.create(
            workspace_id="ws2",
            base_revision="base1",
            source_digest="src2",
            patch_text="+line",
            changed_files=["foo.py"],
            rationale="small surgical diff",
        )
        ledger.add(c2)
        ledger.observe(c2.candidate_id, TestObservation(phase="F2P", selectors=("t1",), passed=("t1",)))
        ledger.accept(c2.candidate_id)

        assert c2.ranking_score > c1.ranking_score


# ---------------------------------------------------------------------------
# Test Docker Executor Failure Classifications
# ---------------------------------------------------------------------------

class TestDockerExecutorFailureKinds:
    def test_failure_kind_timeout(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("PATCHFORGE_DOCKER_IMAGE", "test:latest")
        monkeypatch.setenv("PATCHFORGE_DOCKER_COMMAND", "docker")
        source = tmp_path / "repo"
        source.mkdir()
        manager = WorkspaceManager(tmp_path / "owned")
        ws = manager.create(source, base_revision="b1")
        ex = DockerSelectedTestExecutor(image_tag="test:latest")

        import subprocess
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("docker", 5)):
            obs = ex.run_selected(ws, ["test.py::test_fn"], timeout_seconds=5)
        assert obs.failure_kind == "TIMEOUT"
        assert obs.timed_out is True

    def test_failure_kind_setup_failure(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("PATCHFORGE_DOCKER_IMAGE", "test:latest")
        monkeypatch.setenv("PATCHFORGE_DOCKER_COMMAND", "docker")
        source = tmp_path / "repo"
        source.mkdir()
        manager = WorkspaceManager(tmp_path / "owned")
        ws = manager.create(source, base_revision="b1")
        ex = DockerSelectedTestExecutor(image_tag="test:latest")

        # Pytest collection error or docker container mount error (exit code 2 or 125)
        mock_proc = MagicMock()
        mock_proc.returncode = 2
        mock_proc.stdout = "INTERNALERROR: pytest collection failed"
        mock_proc.stderr = ""
        with patch("subprocess.run", return_value=mock_proc):
            obs = ex.run_selected(ws, ["test.py::test_fn"], timeout_seconds=5)
        assert obs.failure_kind == "SETUP_FAILURE"
        assert obs.exit_code == 2

    def test_failure_kind_test_failure(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("PATCHFORGE_DOCKER_IMAGE", "test:latest")
        monkeypatch.setenv("PATCHFORGE_DOCKER_COMMAND", "docker")
        source = tmp_path / "repo"
        source.mkdir()
        manager = WorkspaceManager(tmp_path / "owned")
        ws = manager.create(source, base_revision="b1")
        ex = DockerSelectedTestExecutor(image_tag="test:latest")

        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stdout = "FAILED test.py::test_fn - AssertionError: 1 != 2"
        mock_proc.stderr = ""
        with patch("subprocess.run", return_value=mock_proc):
            obs = ex.run_selected(ws, ["test.py::test_fn"], timeout_seconds=5)
        assert obs.failure_kind == "TEST_FAILURE"
        assert obs.exit_code == 1
        assert "test.py::test_fn" in obs.failed


# ---------------------------------------------------------------------------
# Test Assertion Failure Guidance in Replanner
# ---------------------------------------------------------------------------

class TestReplannerAssertionEvidence:
    def test_replanner_includes_assertion_evidence_in_guidance(self):
        failure = AssertionFailure(
            test_id="test_req.py::test_socket",
            assertion="assert resp.status_code == 200",
            expected="200",
            actual="500",
            exception="ConnectionError",
        )
        record = ResidualFailureRecord(
            instance_id="psf__requests-2148",
            attempt=1,
            f2p_failed_tests=["test_req.py::test_socket"],
            f2p_total_count=1,
            failing_assertions=[failure],
        )

        decision = FailureDrivenReplanner.decide(
            failure_class="TEST_FAILURE",
            attempt=1,
            max_attempts=3,
            residual_record=record,
        )

        assert decision.action == ReplanAction.REFINE_SEMANTICS
        assert "SMALLEST ACTIONABLE ASSERTION EVIDENCE" in decision.prompt_guidance
        assert "test_req.py::test_socket" in decision.prompt_guidance
        assert "expected=200" in decision.prompt_guidance
        assert "actual=500" in decision.prompt_guidance


# ---------------------------------------------------------------------------
# Test V2RepairReport Markdown Trace
# ---------------------------------------------------------------------------

class TestV2RepairReportTrace:
    def test_trace_markdown_contains_full_audit_information(self):
        report = V2RepairReport(
            instance_id="psf__requests-2148",
            base_revision="fe693c4",
            model="qwen2.5-coder:14b",
            ollama_url="http://localhost:11434",
            docker_image="swebench:latest",
            diagnosis_hypothesis="Socket error in iter_content",
            diagnosis_confidence=0.9,
            localized_files=["requests/models.py"],
            resolved=True,
            f2p_improvement=True,
            best_candidate_id="cand_123",
            harness_f2p_passed=10,
            harness_f2p_total=10,
            harness_p2p_passed=118,
            harness_p2p_total=118,
        )
        report.candidates.append(
            CandidateRecord(
                candidate_id="cand_123",
                parent_candidate_id=None,
                base_revision="fe693c4",
                patch_text="--- a/requests/models.py\n+++ b/requests/models.py\n+pass",
                changed_files=["requests/models.py"],
                rationale="catch socket error",
                status="FINAL",
                ranking_score=1050.0,
                selected_test_passed=["test_iter_content"],
            )
        )
        report.finish()
        trace_md = report.to_markdown_trace()

        assert "# PatchForge V2 Repair Trace — psf__requests-2148" in trace_md
        assert "psf__requests-2148" in trace_md
        assert "fe693c4" in trace_md
        assert "cand_123" in trace_md
        assert "Socket error in iter_content" in trace_md
        assert "10 / 10" in trace_md
        assert "118 / 118" in trace_md
        assert "YES" in trace_md
