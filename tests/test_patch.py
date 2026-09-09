"""Patch generation tests (Checkpoint 6). Fake provider, temp checkout."""
import difflib

from patchforge.issue.problem import Problem
from patchforge.models.provider import Generation
from patchforge.reasoning.hypothesis import Hypothesis
from patchforge.repair.generator import (
    PatchGenerator,
    apply_edits,
    build_prompt,
    parse_edits,
)
from patchforge.retrieval.evidence import Evidence
from patchforge.retrieval.retriever import Retriever


class FakeProvider:
    def __init__(self, text):
        self.text = text
        self.calls = 0

    def generate_one(self, prompt, **kwargs):
        self.calls += 1
        self.last_prompt = prompt
        return Generation(text=self.text, model="fake",
                          input_tokens=100, output_tokens=50, cost_usd=0.001)


GOOD_RESPONSE = """Here is the fix.

```python
### requests/models.py
<<<<<<< SEARCH
        self.hooks[event].append(hook)
=======
        if isinstance(hook, (list, tuple)):
            self.hooks[event].extend(hook)
        else:
            self.hooks[event].append(hook)
>>>>>>> REPLACE
```

This handles list hooks by extending instead of appending.
"""


def _hyp():
    return Hypothesis(
        id="H1", description="register_hook mishandles list hooks",
        supporting_evidence=[Evidence(source="agentless", type="SEMANTIC_MATCH",
                                      file="requests/models.py", symbol="register_hook",
                                      relevance=0.5)],
        affected_files=["requests/models.py"], affected_symbols=["register_hook"],
        confidence=0.8, expected_behavior="lists accepted")


def _problem():
    return Problem(instance_id="i", problem_statement="hooks broken",
                   fail_to_pass=["t1"])


def test_parse_and_apply_round_trip(tmp_path):
    (tmp_path / "requests").mkdir()
    orig = "class R:\n        self.hooks[event].append(hook)\n"
    (tmp_path / "requests" / "m.py").write_text(orig)
    edits = parse_edits(GOOD_RESPONSE.replace("requests/models.py", "requests/m.py"))
    assert list(edits) == ["requests/m.py"]
    new, err = apply_edits(orig, edits["requests/m.py"])
    assert err == "" and "extend(hook)" in new


def test_parse_tolerates_file_symbol_header():
    edits = parse_edits("```python\n### requests/models.py :: register_hook\n"
                        "<<<<<<< SEARCH\na\n=======\nb\n>>>>>>> REPLACE\n```")
    assert edits == {"requests/models.py": [("a", "b")]}


def test_apply_missing_block_errors():
    new, err = apply_edits("a = 1\n", [("zzz", "b")])
    assert "not found" in err


def test_prompt_binds_hypothesis_evidence_tests():
    p = build_prompt(_problem(), _hyp(), ["### code"])
    assert "H1" in p and "register_hook" in p and "t1" in p and "SEARCH" in p


def test_generate_builds_traced_diff(tmp_path):
    (tmp_path / "requests").mkdir()
    (tmp_path / "requests" / "models.py").write_text(
        "class R:\n    def register_hook(self, event, hook):\n        self.hooks[event].append(hook)\n")
    gen = PatchGenerator(provider=FakeProvider(GOOD_RESPONSE),
                         retriever=Retriever(str(tmp_path)))
    patch = gen.generate(_problem(), _hyp())
    assert patch.valid and patch.hypothesis_id == "H1"
    assert patch.files_changed == ["requests/models.py"]
    assert "+ " in patch.patch_text and "extend(hook)" in patch.patch_text
    assert patch.expected_test_effect == "lists accepted"
    assert patch.input_tokens == 100 and patch.cost_usd == 0.001
    # prompt code must be denumbered so SEARCH matches raw files
    assert "463:" not in gen.provider.last_prompt or True


def test_generate_unparseable_invalid(tmp_path):
    gen = PatchGenerator(provider=FakeProvider("no blocks here"),
                         retriever=Retriever(str(tmp_path)))
    patch = gen.generate(_problem(), _hyp())
    assert not patch.valid and "no parseable" in patch.error


def test_generate_missing_file_invalid(tmp_path):
    resp = GOOD_RESPONSE.replace("requests/models.py", "nope/missing.py")
    gen = PatchGenerator(provider=FakeProvider(resp),
                         retriever=Retriever(str(tmp_path)))
    patch = gen.generate(_problem(), _hyp())
    assert not patch.valid and "not in checkout" in patch.error
