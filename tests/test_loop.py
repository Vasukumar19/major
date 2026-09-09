"""Execution + feedback tests (Checkpoint 7). Fakes only, no Docker/LLM."""
from patchforge.integrations.swebench import EvalResult
from patchforge.issue.problem import Problem
from patchforge.localization.candidate import Candidate
from patchforge.repair.loop import RepairLoop
from patchforge.repair.patch import Patch
from patchforge.reasoning.hypothesis import Hypothesis
from patchforge.core.config import PatchForgeConfig
from patchforge.verification.classifier import FailureClass, classify
from patchforge.verification.tester import Tester


def _eval(**kw):
    d = dict(instance_id="i", resolved=False, patch_applied=True,
             fail_to_pass_passed=0, fail_to_pass_total=2,
             pass_to_pass_passed=5, pass_to_pass_total=5)
    d.update(kw)
    return EvalResult(**d)


def _patch(**kw):
    d = dict(hypothesis_id="H1", files_changed=["a.py"], patch_text="diff",
             valid=True, new_contents={"a.py": "x = 1\n"})
    d.update(kw)
    return Patch(**d)


def test_classify_matrix():
    cands = [Candidate(file="a.py", symbol="s")]
    assert classify(_patch(), _eval(resolved=True), cands) == FailureClass.RESOLVED
    assert classify(_patch(), _eval(infra_failure=True), cands) == FailureClass.INFRA_FAILURE
    assert classify(_patch(), _eval(error="Timeout exceeded"), cands) == FailureClass.TIMEOUT
    assert classify(_patch(valid=False, patch_text=""), _eval(), cands) == FailureClass.PATCH_SYNTAX
    assert classify(_patch(), _eval(patch_applied=False), cands) == FailureClass.PATCH_SYNTAX
    assert classify(_patch(), _eval(fail_to_pass_passed=2, pass_to_pass_passed=4)) == FailureClass.REGRESSION
    assert classify(_patch(), _eval(fail_to_pass_passed=1)) == FailureClass.PATCH_SEMANTICS
    assert classify(_patch(), _eval(), cands) == FailureClass.WRONG_HYPOTHESIS
    assert classify(_patch(files_changed=["other.py"]), _eval(), cands) == FailureClass.WRONG_LOCALIZATION
    assert classify(_patch(), _eval(fail_to_pass_total=0, pass_to_pass_total=0)) == FailureClass.TEST_FAILURE


def test_syntax_check():
    t = Tester(swebench=None)
    assert t.syntax_check(_patch())[0] is True
    ok, err = t.syntax_check(_patch(new_contents={"a.py": "def broken(:\n"}))
    assert ok is False and "a.py" in err
    assert t.syntax_check(_patch(valid=False, patch_text=""))[0] is False


class FakeGen:
    def __init__(self, patches):
        self.patches = patches

    def generate(self, problem, hyp, previous_error=""):
        p = dict(self.patches[hyp.id])
        p["hypothesis_id"] = hyp.id
        return Patch(**p)


class FakeTester:
    def __init__(self, reports):
        self.reports = reports
        self.calls = 0
        self.patches = []

    def test(self, instance_id, patch, workdir, run_id):
        from patchforge.verification.tester import TestReport
        self.calls += 1
        self.patches.append(patch)
        return self.reports[self.calls - 1]


def _hyps(*ids):
    return [Hypothesis(id=i, affected_files=["a.py"]) for i in ids]


def test_loop_stops_on_resolve():
    cfg = PatchForgeConfig(max_patch_attempts=3)
    gen = FakeGen({"H1": {"valid": True, "patch_text": "d1", "files_changed": ["a.py"]},
                   "H2": {"valid": True, "patch_text": "d2", "files_changed": ["a.py"]}})
    from patchforge.verification.tester import TestReport
    tester = FakeTester([
        TestReport(True, "", _eval()),
        TestReport(True, "", _eval(resolved=True, fail_to_pass_passed=2)),
    ])
    prob = Problem(instance_id="i")
    cands = [Candidate(file="a.py", symbol="s")]
    res = RepairLoop(cfg, gen, tester).run(prob, _hyps("H1", "H2"), cands, "w", "r")
    assert res.resolved and len(res.attempts) == 2
    assert res.attempts[0]["failure_class"] == "WRONG_HYPOTHESIS"
    assert res.attempts[1]["failure_class"] == "RESOLVED"


def test_loop_bounded_with_retry():
    cfg = PatchForgeConfig(max_patch_attempts=3, max_hypotheses=3)
    gen = FakeGen({"H1": {"valid": True, "patch_text": "d", "files_changed": ["a.py"]}})
    from patchforge.verification.tester import TestReport
    tester = FakeTester([TestReport(True, "", _eval())] * 3)
    res = RepairLoop(cfg, gen, tester).run(Problem(instance_id="i"), _hyps("H1"), [], "w", "r")
    assert not res.resolved and len(res.attempts) == 2  # try + 1 major retry
    assert tester.calls == 2


def test_loop_skips_syntax_failures_without_eval():
    cfg = PatchForgeConfig(max_patch_attempts=3)
    gen = FakeGen({"H1": {"valid": False, "patch_text": "", "error": "no blocks"},
                   "H2": {"valid": True, "patch_text": "d", "files_changed": ["a.py"]}})
    from patchforge.verification.tester import TestReport
    tester = FakeTester([
        TestReport(False, "no blocks", None),
        TestReport(True, "", _eval(resolved=True, fail_to_pass_passed=2)),
    ])
    res = RepairLoop(cfg, gen, tester).run(Problem(instance_id="i"), _hyps("H1", "H2"), [], "w", "r")
    assert res.resolved and res.attempts[0]["failure_class"] == "PATCH_SYNTAX"


class FakeExec:
    def __init__(self, rc=0, out=""):
        self.rc = rc
        self.out = out
        self.cmds = []

    def run_bash(self, command, cwd, timeout=None):
        from patchforge.integrations.minisweagent import ExecResult
        self.cmds.append(command)
        return ExecResult(command=command, output=self.out, returncode=self.rc)


def test_apply_check_gate(tmp_path):
    from patchforge.verification.tester import TestReport
    cfg = PatchForgeConfig(max_patch_attempts=1)
    gen = FakeGen({"H1": {"valid": True, "patch_text": "d", "files_changed": ["a.py"]}})
    tester = FakeTester([TestReport(True, "", _eval(resolved=True, fail_to_pass_passed=2))])
    loop = RepairLoop(cfg, gen, tester, exec_adapter=FakeExec(rc=1, out="error: bad"),
                      repo_dir=str(tmp_path))
    res = loop.run(Problem(instance_id="i"), _hyps("H1"), [], str(tmp_path / "w"), "r")
    assert not res.resolved
    assert res.attempts[0]["failure_class"] == "PATCH_SYNTAX"
    assert tester.patches[0].valid is False, "loop invalidates non-applying patch"
    assert tester.patches[0].error.startswith("git apply --check failed")
