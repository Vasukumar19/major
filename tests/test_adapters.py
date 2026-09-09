"""Adapter tests (Checkpoint 2). No LLM, no Docker: cached artifacts only."""
from patchforge.integrations.agentless import AgentlessAdapter
from patchforge.integrations.minisweagent import MiniSWEAgentAdapter
from patchforge.integrations.repograph import RepoGraphAdapter
from patchforge.integrations.swebench import SWEBenchAdapter
from patchforge.issue.problem import FORBIDDEN_FIELDS, Problem


def test_problem_never_carries_gold():
    row = {"instance_id": "x", "repo": "a/b", "base_commit": "c",
           "problem_statement": "p", "FAIL_TO_PASS": ["t1"], "PASS_TO_PASS": ["t2"],
           "patch": "GOLD-DIFF", "test_patch": "HIDDEN-TESTS"}
    prob = Problem.from_dataset_row(row)
    d = prob.to_dict()
    for f in FORBIDDEN_FIELDS:
        assert f not in d
        assert "GOLD-DIFF" not in str(d) and "HIDDEN-TESTS" not in str(d)
    assert prob.fail_to_pass == ["t1"]


def test_swebench_report_parsing_on_cached_gold_run():
    ad = SWEBenchAdapter()
    rep = ad._find_report("patchforge-gold-smoke3", "psf__requests-863")
    assert rep is not None and rep["psf__requests-863"]["resolved"] is True


def test_repograph_queries_on_cached_graph():
    ad = RepoGraphAdapter()
    pkl = "experiments/baselines/02_repograph/psf__requests-863.graph.pkl"
    ctx = ad.related_symbols(pkl, "register_hook")
    assert "register_hook" in ctx.related
    assert all(e.type == "GRAPH_RELATION" for e in ctx.evidence)
    files = ad.related_files(pkl, "Request")
    assert any(f.endswith("models.py") for f in files.related)
    assert ad.related_symbols(pkl, "no_such_symbol_xyz").related == []


def test_assemble_diff_pure():
    diff = AgentlessAdapter.assemble_diff(
        ["a.py"], ["x = 1\n"], ["x = 2\n"])
    assert "a/a.py" in diff and "-x = 1" in diff and "+x = 2" in diff


def test_minisweagent_bash_no_llm(tmp_path):
    ad = MiniSWEAgentAdapter()
    r = ad.run_bash("echo hello-patchforge", str(tmp_path))
    assert r.returncode == 0 and "hello-patchforge" in r.output
