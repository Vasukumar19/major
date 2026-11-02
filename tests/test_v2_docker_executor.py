"""Tests for DockerSelectedTestExecutor, V2RepairReport, V2RunnerConfig.

All tests are offline — no Docker daemon, Ollama, or network access required.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from patchforge.v2.candidates import CandidateLedger, CandidateStatus, TestObservation
from patchforge.v2.docker_executor import (
    SELECTED_TEST_UNAVAILABLE,
    DockerSelectedTestExecutor,
    _parse_pytest_outcomes,
)
from patchforge.v2.execution import CandidateWorkspaceService
from patchforge.v2.report import CandidateRecord, V2RepairReport
from patchforge.v2.runner import V2RunnerConfig
from patchforge.v2.verification import accepts_residual_candidate
from patchforge.v2.workspaces import WorkspaceManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_workspace(tmp_path: Path, content: str = "x = 1\n"):
    source = tmp_path / "repo"
    source.mkdir()
    (source / "code.py").write_text(content, encoding="utf-8")
    manager = WorkspaceManager(tmp_path / "owned")
    ledger = CandidateLedger(tmp_path / "ledger" / "candidates.jsonl")
    service = CandidateWorkspaceService(manager, ledger)
    ws = service.create_root(source, "abc123")
    return ws, service, ledger


# ---------------------------------------------------------------------------
# DockerSelectedTestExecutor — unavailable paths
# ---------------------------------------------------------------------------

class TestDockerExecutorUnavailable:
    def test_no_selectors_returns_unavailable(self, tmp_path: Path):
        ws, service, _ = _make_workspace(tmp_path)
        ex = DockerSelectedTestExecutor(image_tag="some-image")
        obs = ex.run_selected(ws, [], timeout_seconds=10)
        assert obs.exit_code == -1
        assert SELECTED_TEST_UNAVAILABLE in obs.output

    def test_docker_not_on_path_returns_unavailable(self, tmp_path: Path, monkeypatch):
        monkeypatch.delenv("PATCHFORGE_DOCKER_COMMAND", raising=False)
        monkeypatch.setenv("PATCHFORGE_DOCKER_IMAGE", "swebench:latest")
        ws, service, _ = _make_workspace(tmp_path)
        ex = DockerSelectedTestExecutor(image_tag="swebench:latest")
        with patch("shutil.which", return_value=None):
            obs = ex.run_selected(ws, ["tests/test_foo.py::test_bar"], timeout_seconds=10)
        assert obs.exit_code == -1
        assert SELECTED_TEST_UNAVAILABLE in obs.output

    def test_no_image_configured_returns_unavailable(self, tmp_path: Path, monkeypatch):
        monkeypatch.delenv("PATCHFORGE_DOCKER_IMAGE", raising=False)
        monkeypatch.setenv("PATCHFORGE_DOCKER_COMMAND", "docker")
        ws, service, _ = _make_workspace(tmp_path)
        ex = DockerSelectedTestExecutor(image_tag="")  # no image
        with patch("shutil.which", return_value="/usr/bin/docker"):
            obs = ex.run_selected(ws, ["tests/test_foo.py::test_bar"], timeout_seconds=10)
        assert obs.exit_code == -1
        assert SELECTED_TEST_UNAVAILABLE in obs.output

    def test_timeout_returns_timed_out_observation(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("PATCHFORGE_DOCKER_IMAGE", "swebench:latest")
        monkeypatch.setenv("PATCHFORGE_DOCKER_COMMAND", "docker")
        ws, service, _ = _make_workspace(tmp_path)
        ex = DockerSelectedTestExecutor(image_tag="swebench:latest", timeout_seconds=1)
        import subprocess
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("docker", 1)):
            obs = ex.run_selected(ws, ["tests/test_x.py::test_y"], timeout_seconds=1)
        assert obs.timed_out is True
        assert obs.exit_code is None

    def test_oserror_returns_error_observation(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("PATCHFORGE_DOCKER_IMAGE", "swebench:latest")
        monkeypatch.setenv("PATCHFORGE_DOCKER_COMMAND", "docker")
        ws, service, _ = _make_workspace(tmp_path)
        ex = DockerSelectedTestExecutor(image_tag="swebench:latest")
        with patch("subprocess.run", side_effect=OSError("docker: No such file")):
            obs = ex.run_selected(ws, ["tests/test_x.py::test_y"], timeout_seconds=30)
        assert obs.exit_code == -1
        assert "OSError" in obs.output


# ---------------------------------------------------------------------------
# DockerSelectedTestExecutor — success paths (mocked subprocess)
# ---------------------------------------------------------------------------

class TestDockerExecutorSuccess:
    def _make_mock_proc(self, stdout: str, stderr: str = "", returncode: int = 0):
        proc = MagicMock()
        proc.stdout = stdout
        proc.stderr = stderr
        proc.returncode = returncode
        return proc

    def test_all_pass_produces_correct_observation(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("PATCHFORGE_DOCKER_IMAGE", "swebench:latest")
        monkeypatch.setenv("PATCHFORGE_DOCKER_COMMAND", "docker")
        ws, service, _ = _make_workspace(tmp_path)
        ex = DockerSelectedTestExecutor(image_tag="swebench:latest")
        output = "PASSED tests/test_foo.py::test_bar\n1 passed in 0.5s\n"
        with patch("subprocess.run", return_value=self._make_mock_proc(output)):
            obs = ex.run_selected(ws, ["tests/test_foo.py::test_bar"], timeout_seconds=30)
        assert obs.exit_code == 0
        assert "tests/test_foo.py::test_bar" in obs.passed
        assert obs.failed == ()
        assert obs.timed_out is False

    def test_partial_failure_captured(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("PATCHFORGE_DOCKER_IMAGE", "swebench:latest")
        monkeypatch.setenv("PATCHFORGE_DOCKER_COMMAND", "docker")
        ws, service, _ = _make_workspace(tmp_path)
        ex = DockerSelectedTestExecutor(image_tag="swebench:latest")
        output = (
            "PASSED tests/test_foo.py::test_a\n"
            "FAILED tests/test_foo.py::test_b - AssertionError: 1 != 2\n"
            "1 passed, 1 failed in 1.2s\n"
        )
        with patch("subprocess.run", return_value=self._make_mock_proc(output, returncode=1)):
            obs = ex.run_selected(
                ws,
                ["tests/test_foo.py::test_a", "tests/test_foo.py::test_b"],
                timeout_seconds=30,
            )
        assert "tests/test_foo.py::test_a" in obs.passed
        assert any("test_b" in f for f in obs.failed)


# ---------------------------------------------------------------------------
# _parse_pytest_outcomes
# ---------------------------------------------------------------------------

class TestParsePytestOutcomes:
    def test_empty_output(self):
        passed, failed = _parse_pytest_outcomes("", ["sel"])
        assert passed == []
        assert failed == []

    def test_passed_and_failed_lines(self):
        output = "PASSED tests/x.py::test_one\nFAILED tests/x.py::test_two - assert 0\n"
        passed, failed = _parse_pytest_outcomes(output, ["tests/x.py::test_one"])
        assert passed == ["tests/x.py::test_one"]
        assert failed == ["tests/x.py::test_two - assert 0"]

    def test_error_lines_captured(self):
        output = "ERROR tests/x.py::test_import\n"
        passed, failed = _parse_pytest_outcomes(output, [])
        assert "tests/x.py::test_import" in failed


# ---------------------------------------------------------------------------
# accepts_residual_candidate
# ---------------------------------------------------------------------------

class TestAcceptsResidualCandidate:
    def test_timed_out_not_accepted(self):
        obs = TestObservation(phase="F2P", selectors=("a",), timed_out=True)
        assert accepts_residual_candidate(obs) is False

    def test_no_passed_not_accepted(self):
        obs = TestObservation(phase="F2P", selectors=("a",), failed=("a",))
        assert accepts_residual_candidate(obs) is False

    def test_passed_no_regression_accepted(self):
        obs = TestObservation(phase="F2P", selectors=("a",), passed=("a",))
        assert accepts_residual_candidate(obs) is True

    def test_passed_with_regression_rejected(self):
        f2p = TestObservation(phase="F2P", selectors=("a",), passed=("a",))
        p2p = TestObservation(phase="P2P", selectors=("b",), failed=("b",))
        assert accepts_residual_candidate(f2p, p2p) is False

    def test_passed_with_clean_p2p_accepted(self):
        f2p = TestObservation(phase="F2P", selectors=("a",), passed=("a",))
        p2p = TestObservation(phase="P2P", selectors=("b",), passed=("b",))
        assert accepts_residual_candidate(f2p, p2p) is True


# ---------------------------------------------------------------------------
# V2RepairReport — serialisation round-trip
# ---------------------------------------------------------------------------

class TestV2RepairReport:
    def test_roundtrip(self, tmp_path: Path):
        report = V2RepairReport(
            instance_id="psf__requests-2148",
            base_revision="abc123",
            model="qwen2.5-coder:14b",
            ollama_url="http://localhost:11434",
            docker_image="swebench:latest",
        )
        report.candidates.append(
            CandidateRecord(
                candidate_id="cand1",
                parent_candidate_id=None,
                base_revision="abc123",
                patch_text="--- a/foo.py\n+++ b/foo.py\n",
                changed_files=["foo.py"],
                rationale="initial",
                status="PENDING",
            )
        )
        report.finish()
        dest = tmp_path / "report.json"
        report.save(dest)
        loaded = V2RepairReport.load(dest)
        assert loaded.instance_id == "psf__requests-2148"
        assert len(loaded.candidates) == 1
        assert loaded.candidates[0].candidate_id == "cand1"
        assert loaded.elapsed_seconds >= 0

    def test_summary_contains_key_fields(self):
        report = V2RepairReport(
            instance_id="psf__requests-2148",
            base_revision="abc",
            model="qwen",
            ollama_url="",
            docker_image="",
            resolved=True,
            f2p_improvement=True,
        )
        report.finish()
        s = report.summary()
        assert "psf__requests-2148" in s
        assert "True" in s

    def test_notes_capped_at_five_in_summary(self):
        report = V2RepairReport(
            instance_id="x", base_revision="y", model="z",
            ollama_url="", docker_image="",
        )
        for i in range(10):
            report.notes.append(f"note {i}")
        report.finish()
        s = report.summary()
        # summary() only shows last 5 notes
        assert s.count("note") <= 5


# ---------------------------------------------------------------------------
# V2RunnerConfig — from_environment
# ---------------------------------------------------------------------------

class TestV2RunnerConfig:
    def test_defaults_without_env(self, monkeypatch):
        for var in [
            "PATCHFORGE_OLLAMA_URL", "OLLAMA_HOST",
            "PATCHFORGE_DOCKER_COMMAND", "PATCHFORGE_DOCKER_IMAGE",
            "SWEBENCH_HARNESS_TIMEOUT",
        ]:
            monkeypatch.delenv(var, raising=False)
        cfg = V2RunnerConfig.from_environment()
        assert cfg.ollama_url == ""
        assert cfg.docker_command == ""
        assert cfg.harness_timeout_seconds == 300

    def test_reads_ollama_url_from_env(self, monkeypatch):
        monkeypatch.setenv("PATCHFORGE_OLLAMA_URL", "http://localhost:11434")
        cfg = V2RunnerConfig.from_environment()
        assert cfg.ollama_url == "http://localhost:11434"

    def test_overrides_applied(self, monkeypatch):
        monkeypatch.setenv("PATCHFORGE_OLLAMA_URL", "http://localhost:11434")
        cfg = V2RunnerConfig.from_environment(model="llama3:8b", max_refinement_cycles=5)
        assert cfg.model == "llama3:8b"
        assert cfg.max_refinement_cycles == 5

    def test_timeout_from_env(self, monkeypatch):
        monkeypatch.setenv("SWEBENCH_HARNESS_TIMEOUT", "600")
        cfg = V2RunnerConfig.from_environment()
        assert cfg.harness_timeout_seconds == 600

    def test_invalid_timeout_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("SWEBENCH_HARNESS_TIMEOUT", "not_a_number")
        cfg = V2RunnerConfig.from_environment()
        assert cfg.harness_timeout_seconds == 300
