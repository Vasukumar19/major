"""Unit tests for Diagnostic Validation Probes in PatchForge v0.3."""
from pathlib import Path
import pytest

from patchforge.reasoning.hypothesis import Hypothesis
from patchforge.reasoning.probes import (
    ProbeResult,
    StaticProbe,
    TestProbe,
    ReproductionProbe,
    TracebackProbe,
)
from patchforge.retrieval.evidence import Evidence


@pytest.fixture
def sample_code_dir(tmp_path):
    f = tmp_path / "models.py"
    f.write_text("class Request:\n    def prepare_cookies(self):\n        pass\n", encoding="utf-8")
    return tmp_path


def test_static_probe_supports(sample_code_dir):
    hypo = Hypothesis(
        id="H1",
        description="Cookie handling missing in Request",
        supporting_evidence=[
            Evidence(source="test", type="STATIC_REFERENCE", file="models.py", symbol="prepare_cookies"),
        ],
    )
    probe = StaticProbe()
    res = probe.run(hypo, {"repo_dir": str(sample_code_dir)})
    assert res.supports is True
    assert res.contradicts is False
    assert "prepare_cookies" in res.observation


def test_static_probe_contradicts_missing_file(sample_code_dir):
    hypo = Hypothesis(
        id="H2",
        description="Nonexistent file referenced",
        supporting_evidence=[
            Evidence(source="test", type="STATIC_REFERENCE", file="nonexistent.py", symbol="foo"),
        ],
    )
    probe = StaticProbe()
    res = probe.run(hypo, {"repo_dir": str(sample_code_dir)})
    assert res.contradicts is True
    assert res.supports is False


def test_test_probe_evaluation():
    hypo = Hypothesis(
        id="H1",
        description="Fix failing cookie test",
        supporting_evidence=[
            Evidence(source="test", type="STATIC_REFERENCE", file="tests/test_cookies.py", explanation="assert cookie header"),
        ],
    )
    probe = TestProbe()
    res = probe.run(hypo, {"failing_tests": ["tests/test_cookies.py::test_cookie_header"]})
    assert res.supports is True
    assert res.probe_type == "TEST"


def test_traceback_probe_evaluation():
    hypo = Hypothesis(
        id="H1",
        description="Fix exception in models.py",
        supporting_evidence=[
            Evidence(source="test", type="STATIC_REFERENCE", file="requests/models.py"),
        ],
    )
    probe = TracebackProbe()
    tb = "File 'requests/models.py', line 123, in prepare_url\n  KeyError: 'url'"
    res = probe.run(hypo, {"traceback": tb})
    assert res.supports is True
    assert "requests/models.py" in res.observation
