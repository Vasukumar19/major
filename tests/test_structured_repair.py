"""Unit tests for StructuredRepair model contract, parser, ranking, and end-to-end flow."""
import json
import unittest

from patchforge.core.target import EditSite
from patchforge.issue.problem import Problem
from patchforge.repair.planner import RepairUnitPlanner
from patchforge.repair.ranking import RepairSiteRanker
from patchforge.repair.reconstructor import SourceReconstructor
from patchforge.repair.schema import (
    RepairAction,
    RepairUnit,
    RepairUnitType,
    StructuredRepairOutput,
)
from patchforge.repair.structured_repair import (
    StructuredRepairParser,
    StructuredRepairPromptBuilder,
)
from patchforge.repair.validator import StaticRepairValidator
from patchforge.repository_intelligence.diagnosis import DiagnosisResult


class TestStructuredRepair(unittest.TestCase):

    def setUp(self):
        self.dummy_unit = RepairUnit(
            id="test::func::STATEMENT::10-10",
            file_path="foo.py",
            symbol="func",
            unit_type=RepairUnitType.STATEMENT,
            node_type="Assign",
            start_line=10,
            end_line=10,
            source_text="x = 1\n",
            indentation="    ",
        )

    def test_parse_clean_json(self):
        resp = json.dumps({
            "repair_action": "replace_statement",
            "target_unit_id": self.dummy_unit.id,
            "target_symbol": "func",
            "replacement": "x = 42",
            "reasoning": "Update initial state",
            "invariant": "Preserve type int",
        })
        out = StructuredRepairParser.parse(resp, self.dummy_unit)
        self.assertEqual(out.repair_action, "replace_statement")
        self.assertEqual(out.replacement, "x = 42")
        self.assertEqual(out.reasoning, "Update initial state")

    def test_parse_markdown_wrapped_json(self):
        resp = (
            "Here is the structured repair:\n"
            "```json\n"
            "{\n"
            '  "repair_action": "replace_statement",\n'
            f'  "target_unit_id": "{self.dummy_unit.id}",\n'
            '  "replacement": "x = 99",\n'
            '  "reasoning": "Fix off-by-one"\n'
            "}\n"
            "```\n"
            "This fixes the bug cleanly."
        )
        out = StructuredRepairParser.parse(resp, self.dummy_unit)
        self.assertEqual(out.repair_action, "replace_statement")
        self.assertEqual(out.replacement, "x = 99")

    def test_parse_with_think_tags(self):
        resp = (
            "<think>\n"
            "The root cause is x being 1 instead of 0.\n"
            "</think>\n"
            "```json\n"
            "{\n"
            '  "repair_action": "replace_statement",\n'
            f'  "target_unit_id": "{self.dummy_unit.id}",\n'
            '  "replacement": "x = 0"\n'
            "}\n"
            "```"
        )
        out = StructuredRepairParser.parse(resp, self.dummy_unit)
        self.assertEqual(out.replacement, "x = 0")

    def test_parse_code_fence_fallback(self):
        resp = (
            "I will update this statement directly:\n"
            "```python\n"
            "x = 100\n"
            "```\n"
        )
        out = StructuredRepairParser.parse(resp, self.dummy_unit)
        self.assertEqual(out.replacement, "x = 100")
        self.assertEqual(out.repair_action, "replace_statement")

    def test_repair_site_ranking(self):
        cands = [
            EditSite(file_path="pkg/util.py", symbol="cached_eval", line_start=10, line_end=20, site_role="RELATED_HELPER"),
            EditSite(file_path="pkg/evaluator.py", symbol="_istrue", line_start=50, line_end=60, site_role="CALL_SITE"),
            EditSite(file_path="pkg/unrelated.py", symbol="unrelated_func", line_start=1, line_end=10, site_role="PRIMARY"),
        ]
        prob = Problem(
            instance_id="test-1",
            repo="test/repo",
            base_commit="abc",
            problem_statement="Failure in _istrue evaluation condition",
        )
        traceback = "Traceback:\n  File 'pkg/evaluator.py', line 55, in _istrue\n    result = cached_eval(expr)\n"
        diag = DiagnosisResult(
            cause="_istrue mishandles dynamic expressions",
            invariant="preserve evaluator",
            repair_strategy="direct eval in _istrue",
            affected_sites=["pkg/evaluator.py:_istrue"],
            raw_response="",
        )

        ranker = RepairSiteRanker(graph=None)
        ranked = ranker.rank_sites(cands, problem=prob, diagnosis=diag, test_traceback=traceback, primary_file="pkg/evaluator.py")

        self.assertTrue(len(ranked) >= 2)
        # _istrue should be top ranked due to traceback presence + diagnosis match + call site preference
        self.assertEqual(ranked[0].symbol, "_istrue")
        self.assertTrue(ranked[0].in_failing_traceback)
        self.assertTrue(ranked[0].score > ranked[1].score)

    def test_end_to_end_mock_repair_flow(self):
        source = (
            "class MarkEvaluator:\n"
            "    def _istrue(self, expr):\n"
            "        result = cached_eval(expr)\n"
            "        return result\n"
        )
        # 1. Candidate
        cand = EditSite(file_path="eval.py", symbol="_istrue", line_start=2, line_end=4, site_role="CALL_SITE")

        # 2. Ranking
        ranker = RepairSiteRanker()
        ranked = ranker.rank_sites([cand], primary_file="eval.py")
        top_site = ranked[0]

        # 3. Planning
        unit = RepairUnitPlanner.plan_repair_unit(
            file_path=top_site.file_path,
            source_code=source,
            target_symbol=top_site.symbol,
            target_lines=(3, 3),
        )
        self.assertEqual(unit.start_line, 3)

        # 4. Prompt
        prompt = StructuredRepairPromptBuilder.build_repair_prompt(unit=unit, full_source=source)
        self.assertIn("MarkEvaluator", prompt)

        # 5. Mock Model Response
        mock_resp = json.dumps({
            "repair_action": "replace_statement",
            "target_unit_id": unit.id,
            "target_symbol": unit.symbol,
            "replacement": "result = eval(expr, globals())",
            "reasoning": "Direct eval avoids cross-module cache collision",
        })
        structured_out = StructuredRepairParser.parse(mock_resp, unit)

        # 6. Reconstruction
        reconstructed = SourceReconstructor.reconstruct_single(source, unit, structured_out, "eval.py")
        self.assertTrue(reconstructed.success)
        self.assertIn("result = eval(expr, globals())", reconstructed.modified_contents["eval.py"])
        self.assertIn("+        result = eval(expr, globals())", reconstructed.patch_text)

        # 7. Static Validation
        val = StaticRepairValidator.validate({"eval.py": source}, reconstructed, [unit])
        self.assertTrue(val.valid)
        self.assertTrue(val.syntax_ok)
        self.assertTrue(val.target_changed)
        self.assertTrue(val.unrelated_targets_preserved)


if __name__ == "__main__":
    unittest.main()
