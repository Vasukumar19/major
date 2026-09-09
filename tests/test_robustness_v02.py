import pytest
from patchforge.repair.generator import apply_edits_detailed, MatchStatus, validate_ast
from patchforge.repair.patch import Patch
from patchforge.repair.loop import RepairLoop
from patchforge.core.config import PatchForgeConfig
from patchforge.issue.problem import Problem
from patchforge.reasoning.hypothesis import Hypothesis
from patchforge.verification.classifier import FailureClass


def test_exact_search_match():
    orig = "def foo():\n    return 42\n"
    edits = [("return 42", "return 100")]
    new, tier, err = apply_edits_detailed(orig, edits)
    assert err == ""
    assert tier == MatchStatus.EXACT.value
    assert "return 100" in new


def test_trailing_whitespace_and_crlf_match():
    orig = "def foo():   \r\n    return 42   \r\n"
    edits = [("def foo():\n    return 42", "def foo():\n    return 100")]
    new, tier, err = apply_edits_detailed(orig, edits)
    assert err == ""
    assert tier == MatchStatus.WHITESPACE_NORMALIZED.value
    assert "return 100" in new


def test_indentation_normalized_match():
    orig = "class A:\n    def foo():\n        return 42\n"
    # Search block has 0 base indent, but target has 4-space indent
    edits = [("def foo():\n    return 42", "def foo():\n    return 100")]
    new, tier, err = apply_edits_detailed(orig, edits)
    assert err == ""
    assert tier == MatchStatus.INDENTATION_NORMALIZED.value
    assert "        return 100" in new


def test_ambiguous_match_rejection():
    # 'return 42' appears in 2 separate places
    orig = "def f1():\n    return 42\ndef f2():\n    return 42\n"
    edits = [("return 42", "return 100")]
    new, tier, err = apply_edits_detailed(orig, edits)
    assert tier == MatchStatus.AMBIGUOUS.value
    assert "ambiguous" in err


def test_not_found_match():
    orig = "def foo():\n    return 42\n"
    edits = [("non_existent_function()", "pass")]
    new, tier, err = apply_edits_detailed(orig, edits)
    assert tier == MatchStatus.NOT_FOUND.value
    assert "not found" in err


def test_ast_validation_valid():
    valid_code = "def foo(x: int) -> int:\n    return x + 1\n"
    ok, err = validate_ast(valid_code, "test.py")
    assert ok is True
    assert err == ""


def test_ast_validation_invalid_syntax():
    invalid_code = "def foo(x:\n    return x +\n"
    ok, err = validate_ast(invalid_code, "test.py")
    assert ok is False
    assert "test.py" in err or "syntax" in err.lower()


class DummyEval:
    def __init__(self, f2p_p, f2p_t, p2p_p, p2p_t, resolved=False, error=""):
        self.instance_id = "test-1"
        self.resolved = resolved
        self.patch_applied = True
        self.fail_to_pass_passed = f2p_p
        self.fail_to_pass_total = f2p_t
        self.pass_to_pass_passed = p2p_p
        self.pass_to_pass_total = p2p_t
        self.infra_failure = False
        self.error = error
        self.run_id = "run-1"


class DummyReport:
    def __init__(self, ev, syntax_ok=True):
        self.eval = ev
        self.syntax_ok = syntax_ok
        self.syntax_error = ""

    def to_dict(self):
        return {"syntax_ok": self.syntax_ok, "eval": vars(self.eval) if self.eval else {}}


class DummyTester:
    def __init__(self, reports):
        self.reports = list(reports)
        self.call_count = 0

    def test(self, instance_id, patch, attempt_dir, run_id):
        rep = self.reports[min(self.call_count, len(self.reports) - 1)]
        self.call_count += 1
        return rep


class DummyGenerator:
    def __init__(self):
        self.calls = []

    def generate(self, problem, hyp, previous_error=""):
        self.calls.append({"hyp_id": hyp.id, "prev_error": previous_error})
        return Patch(hypothesis_id=hyp.id, files_changed=["a.py"], patch_text="diff", valid=True)


def test_near_miss_retention_when_f2p_high_and_no_regression():
    # Attempt 1 passes 9/10 F2P with 0 regression -> Attempt 2 should retain H1 with feedback
    r1 = DummyReport(DummyEval(f2p_p=9, f2p_t=10, p2p_p=50, p2p_t=50, resolved=False, error="AssertionError on test_edge"))
    r2 = DummyReport(DummyEval(f2p_p=10, f2p_t=10, p2p_p=50, p2p_t=50, resolved=True))

    gen = DummyGenerator()
    tester = DummyTester([r1, r2])
    loop = RepairLoop(PatchForgeConfig(max_patch_attempts=3), gen, tester)

    prob = Problem("test-1", "repo", "base", "desc", fail_to_pass=["t1", "t2"], pass_to_pass=["p1"])
    h1 = Hypothesis("H1", "desc 1", [], [], ["a.py"], ["f"], 0.9, "exp")
    h2 = Hypothesis("H2", "desc 2", [], [], ["b.py"], ["g"], 0.8, "exp")

    res = loop.run(prob, [h1, h2], [], "workdir", "run-1")

    assert res.resolved is True
    assert len(res.attempts) == 2
    assert gen.calls[1]["hyp_id"] == "H1"
    assert "passed 9/10 F2P" in gen.calls[1]["prev_error"]
    assert res.attempts[1]["retained"] is True


def test_no_retention_when_regression_occurs():
    # Attempt 1 passes 9/10 F2P but causes 5 P2P regressions (45/50) -> should NOT retain H1
    r1 = DummyReport(DummyEval(f2p_p=9, f2p_t=10, p2p_p=45, p2p_t=50, resolved=False))
    r2 = DummyReport(DummyEval(f2p_p=0, f2p_t=10, p2p_p=50, p2p_t=50, resolved=False))

    gen = DummyGenerator()
    tester = DummyTester([r1, r2])
    loop = RepairLoop(PatchForgeConfig(max_patch_attempts=2), gen, tester)

    prob = Problem("test-1", "repo", "base", "desc", fail_to_pass=["t1"], pass_to_pass=["p1"])
    h1 = Hypothesis("H1", "desc 1", [], [], ["a.py"], ["f"], 0.9, "exp")
    h2 = Hypothesis("H2", "desc 2", [], [], ["b.py"], ["g"], 0.8, "exp")

    res = loop.run(prob, [h1, h2], [], "workdir", "run-1")

    assert len(res.attempts) == 2
    assert gen.calls[1]["hyp_id"] == "H2"
    assert res.attempts[1]["retained"] is False


def test_no_retention_when_zero_f2p_passed():
    # Attempt 1 passes 0/10 F2P and 50/50 P2P -> should NOT retain H1
    r1 = DummyReport(DummyEval(f2p_p=0, f2p_t=10, p2p_p=50, p2p_t=50, resolved=False))
    r2 = DummyReport(DummyEval(f2p_p=0, f2p_t=10, p2p_p=50, p2p_t=50, resolved=False))

    gen = DummyGenerator()
    tester = DummyTester([r1, r2])
    loop = RepairLoop(PatchForgeConfig(max_patch_attempts=2), gen, tester)

    prob = Problem("test-1", "repo", "base", "desc", fail_to_pass=["t1"], pass_to_pass=["p1"])
    h1 = Hypothesis("H1", "desc 1", [], [], ["a.py"], ["f"], 0.9, "exp")
    h2 = Hypothesis("H2", "desc 2", [], [], ["b.py"], ["g"], 0.8, "exp")

    res = loop.run(prob, [h1, h2], [], "workdir", "run-1")

    assert len(res.attempts) == 2
    assert gen.calls[1]["hyp_id"] == "H2"
