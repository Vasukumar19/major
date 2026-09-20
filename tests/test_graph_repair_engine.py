"""Unit tests for GraphRepairEngine end-to-end execution."""
import os
import shutil
import tempfile
import pytest

from patchforge.issue.problem import Problem
from patchforge.models.provider import Generation, ModelProvider
from patchforge.pipeline.graph_engine import GraphRepairEngine


class MockDiagnosisAndPatchProvider(ModelProvider):
    def __init__(self):
        self.call_count = 0

    def generate_one(self, prompt: str, system: str = "", max_tokens: int = 1024, temperature: float = 0.0) -> Generation:
        self.call_count += 1
        if "Principal Software Diagnosis Engine" in system or "=== CAUSE ===" in prompt:
            # Diagnosis response
            text = """=== CAUSE ===
The send_request method does not validate url scheme.

=== INVARIANT ===
Preserve connection pool for existing valid urls.

=== REPAIR STRATEGY ===
Add url validation before connecting.

=== AFFECTED SITES ===
client.py:send_request
"""
            return Generation(text=text, model="mock-14b", input_tokens=500, output_tokens=80)
        else:
            # Structured repair synthesis response
            text = """```json
{
  "repair_action": "replace_method",
  "target_unit_id": "client.py::send_request::METHOD::5-7",
  "target_symbol": "send_request",
  "replacement": "    def send_request(self, method, url):\\n        if not url:\\n            raise ValueError('Invalid URL')\\n        self.connect()\\n        return f\\"OK: {method} {url}\\"",
  "reasoning": "Add url validation check",
  "invariant": "Preserve existing valid urls",
  "confidence": 0.95
}
```"""
            return Generation(text=text, model="mock-14b", input_tokens=800, output_tokens=100)


def test_graph_repair_engine_flow():
    tmp_dir = tempfile.mkdtemp(prefix="pf_test_repo_")
    try:
        # Create a tiny git repo
        client_code = """class HttpClient:
    def connect(self):
        pass

    def send_request(self, method, url):
        self.connect()
        return f"OK: {method} {url}"
"""
        client_path = os.path.join(tmp_dir, "client.py")
        with open(client_path, "w", encoding="utf-8") as f:
            f.write(client_code)

        import subprocess
        subprocess.run(["git", "init"], cwd=tmp_dir, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_dir, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_dir, capture_output=True)
        subprocess.run(["git", "add", "."], cwd=tmp_dir, capture_output=True)
        subprocess.run(["git", "commit", "-m", "initial"], cwd=tmp_dir, capture_output=True)

        prob = Problem(
            instance_id="test_repo__test-1",
            repo="test_repo",
            base_commit="HEAD",
            problem_statement="send_request should validate empty url",
        )

        mock_provider = MockDiagnosisAndPatchProvider()
        engine = GraphRepairEngine(provider=mock_provider, model_name="mock-14b", workspace=tmp_dir)

        result = engine.repair(prob, repo_dir=tmp_dir, tester=None)

        assert result.target is not None
        assert result.target.symbol == "send_request"
        assert result.diagnosis is not None
        assert "validate url scheme" in result.diagnosis.cause
        assert result.patch_applied is True
        assert result.patch is not None
        assert "Invalid URL" in result.patch.patch_text

        # Verify episode was saved
        episode_file = os.path.join("results", "episodes", "test_repo__test-1.json")
        assert os.path.exists(episode_file)

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
