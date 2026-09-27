"""Unit tests for Dynamic Sandboxed Probing Subsystem and Executable Counterfactuals."""
from __future__ import annotations

import tempfile
from pathlib import Path
from patchforge.diagnosis.diagnosis import CompetingDiagnosisResult, DiagnosisHypothesis
from patchforge.experiment.injector import ProbeInjector
from patchforge.experiment.observer import ProbeObserver
from patchforge.experiment.planner import ExperimentPlanner
from patchforge.experiment.counterfactual import CounterfactualEngine
from patchforge.experiment.probe import ProbeSpecification, ProbeType


def test_probe_lifecycle_and_rollback():
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_p = Path(tmpdir)
        test_file = repo_p / "sub.py"
        test_file.write_text("def my_func(a, b):\n    res = a + b\n    return res\n", encoding="utf-8")

        injector = ProbeInjector(tmpdir)
        probes = [
            ProbeSpecification(
                probe_id="P1",
                probe_type=ProbeType.VARIABLE_VALUE,
                file_path="sub.py",
                symbol="my_func",
                target_line=2,
                variable_name="res",
            ),
            ProbeSpecification(
                probe_id="P_ENTRY",
                probe_type=ProbeType.FUNCTION_ENTRY,
                file_path="sub.py",
                symbol="my_func",
                target_line=1,
            ),
        ]

        # 1. Inject
        injector.inject_probes(probes)
        mod_text = test_file.read_text(encoding="utf-8")
        assert "__PF_PROBE__:P1" in mod_text
        assert "__PF_PROBE__:P_ENTRY" in mod_text

        # 2. Simulate execution output
        sim_log = """
running test...
__PF_PROBE__:P_ENTRY:ENTER
__PF_PROBE__:P1:42
test finished with status: ok
"""
        observations = ProbeObserver.parse_output(sim_log)
        assert len(observations) == 2
        obs_map = {o.probe_id: o.observed_value for o in observations}
        assert obs_map["P_ENTRY"] == "ENTER"
        assert obs_map["P1"] == "42"

        # 3. Counterfactual analysis
        diag = CompetingDiagnosisResult(
            hypotheses=[
                DiagnosisHypothesis(id="A", cause="Variable res is computed properly", confidence=0.7),
                DiagnosisHypothesis(id="B", cause="Variable res is None", confidence=0.7),
            ]
        )
        res = CounterfactualEngine.evaluate_observations(probes, observations, diag)
        assert res.supported_hypothesis == "A"
        assert "B" in res.contradicted_hypotheses
        assert diag.hypotheses[0].confidence > 0.7
        assert diag.hypotheses[1].confidence < 0.7

        # 4. Rollback
        injector.restore_all()
        restored = test_file.read_text(encoding="utf-8")
        assert "__PF_PROBE__" not in restored
        assert restored == "def my_func(a, b):\n    res = a + b\n    return res\n"
