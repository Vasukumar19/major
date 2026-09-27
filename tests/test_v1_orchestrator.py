"""Integration test for PatchForge V1 Autonomous Software-Repair Orchestrator."""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from patchforge.issue.problem import Problem
from patchforge.models.provider import Generation, ModelProvider
from patchforge.pipeline.v1_orchestrator import V1RepairOrchestrator, V1OrchestratorResult


class MockV1ModelProvider(ModelProvider):
    """Deterministic mock provider simulating competing diagnosis and structured repair."""

    def __init__(self):
        self.model = "mock-v1"

    def generate_one(self, prompt: str, system: Optional[str] = None, **kwargs) -> ModelOutput:
        # If diagnosis prompt
        if "Principal Software Diagnosis Engine" in (system or "") or "### ISSUE SPECIFICATION" in prompt:
            diag_text = """
=== CAUSE ===
Session.merge_environment_settings drops proxies if not explicitly provided in request.

=== EXPECTED BEHAVIOR ===
Preserve environment proxies when request proxies are None.

=== INVARIANT ===
Do not overwrite session proxies with empty dict.

=== EVIDENCE ===
- State flow shows proxies dict overwrite at line 15.

=== DEFECT CONFIDENCE ===
0.95

=== REPAIR STRATEGY ===
Only update proxies when kwargs contains non-None proxies.

=== REPAIR JUSTIFICATION ===
Prevents proxy regression on session reuse.

=== AFFECTED SITES ===
requests/sessions.py:Session.merge_environment_settings
"""
            return Generation(text=diag_text, model="mock-v1", input_tokens=100, output_tokens=50)

        # If structured repair prompt
        repair_json = """
```json
{
  "repair_action": "replace_statement",
  "target_unit_id": "requests/sessions.py::merge_environment_settings::STATEMENT::15-15",
  "replacement": "if proxies is not None:\\n    settings['proxies'] = proxies",
  "reasoning": "Only assign proxies if explicitly passed",
  "invariant": "Preserve existing proxies",
  "confidence": 0.95
}
```
"""
        return Generation(text=repair_json, model="mock-v1", input_tokens=150, output_tokens=80)


def test_v1_orchestrator_end_to_end():
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_p = Path(tmpdir)

        # Initialize mock git repository
        subprocess.run(["git", "init"], cwd=tmpdir, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "Tester"], cwd=tmpdir, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmpdir, capture_output=True)

        req_dir = repo_p / "requests"
        req_dir.mkdir(parents=True, exist_ok=True)
        sess_file = req_dir / "sessions.py"
        sess_code = """class Session:
    def __init__(self):
        self.proxies = {}

    def merge_environment_settings(self, url, proxies, stream, verify, cert):
        settings = {}
        settings['proxies'] = proxies
        return settings
"""
        sess_file.write_text(sess_code, encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=tmpdir, capture_output=True)
        subprocess.run(["git", "commit", "-m", "fix: initial commit with proxy bug"], cwd=tmpdir, capture_output=True)

        prob = Problem(
            instance_id="test__requests-2317",
            repo="psf/requests",
            base_commit="HEAD",
            problem_statement="Session.merge_environment_settings overwrites proxies with None.",
            hints_text="Check merge_environment_settings in requests/sessions.py",
        )

        mock_provider = MockV1ModelProvider()
        orchestrator = V1RepairOrchestrator(
            provider=mock_provider,
            model_name="mock-v1",
            episodes_dir=str(repo_p / "episodes"),
        )

        result: V1OrchestratorResult = orchestrator.repair(
            problem=prob,
            repo_dir=tmpdir,
            tester=None,
            max_repair_attempts=2,
            allow_dynamic_probes=False,
        )

        assert result.instance_id == "test__requests-2317"
        assert result.target is not None
        assert "sessions.py" in result.target.file_path
        assert result.diagnosis is not None
        assert len(result.diagnosis.hypotheses) >= 1
        assert result.repair_unit is not None
        assert result.patch is not None
        assert result.validation_result is not None
        assert result.validation_result.valid
        assert "+        settings['proxies'] = proxies" in result.patch.patch_text or "proxies is not None" in result.patch.patch_text
