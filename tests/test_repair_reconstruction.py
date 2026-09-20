"""Unit tests for SourceReconstructor and StaticRepairValidator."""
import ast
import unittest

from patchforge.repair.reconstructor import SourceReconstructor
from patchforge.repair.schema import (
    RepairAction,
    RepairUnit,
    RepairUnitType,
    StructuredRepairOutput,
)
from patchforge.repair.validator import StaticRepairValidator


class TestRepairReconstruction(unittest.TestCase):

    def test_expression_replacement(self):
        code = (
            "def check(x):\n"
            "    return cached_eval(x)\n"
        )
        unit = RepairUnit(
            id="test::check::EXPRESSION::2:11",
            file_path="mod.py",
            symbol="check",
            unit_type=RepairUnitType.EXPRESSION,
            node_type="Call",
            start_line=2,
            end_line=2,
            start_col=11,
            end_col=25,
            source_text="cached_eval(x)",
            indentation="    ",
        )
        out = StructuredRepairOutput(
            repair_action="replace_expression",
            target_unit_id=unit.id,
            replacement="eval(x, globals())",
        )
        rec = SourceReconstructor.reconstruct_single(code, unit, out, "mod.py")
        self.assertTrue(rec.success)
        self.assertIn("eval(x, globals())", rec.modified_contents["mod.py"])
        self.assertIn("-    return cached_eval(x)", rec.patch_text)
        self.assertIn("+    return eval(x, globals())", rec.patch_text)

        # Validate
        val = StaticRepairValidator.validate({"mod.py": code}, rec, [unit])
        self.assertTrue(val.valid)
        self.assertTrue(val.syntax_ok)
        self.assertTrue(val.target_changed)

    def test_statement_replacement_indentation_preserved(self):
        code = (
            "class A:\n"
            "    def run(self):\n"
            "        x = 1\n"
            "        return x\n"
        )
        unit = RepairUnit(
            id="test::run::STATEMENT::3-3",
            file_path="app.py",
            symbol="run",
            unit_type=RepairUnitType.STATEMENT,
            node_type="Assign",
            start_line=3,
            end_line=3,
            source_text="        x = 1\n",
            indentation="        ",
        )
        out = StructuredRepairOutput(
            repair_action="replace_statement",
            target_unit_id=unit.id,
            replacement="x = 42",  # Model sends un-indented or indented code
        )
        rec = SourceReconstructor.reconstruct_single(code, unit, out, "app.py")
        self.assertTrue(rec.success)
        new_source = rec.modified_contents["app.py"]
        self.assertIn("        x = 42\n", new_source)

        val = StaticRepairValidator.validate({"app.py": code}, rec, [unit])
        self.assertTrue(val.valid)
        self.assertTrue(val.unrelated_targets_preserved)

    def test_statement_block_replacement(self):
        code = (
            "def process(items):\n"
            "    for item in items:\n"
            "        if item > 0:\n"
            "            yield item\n"
            "    return\n"
        )
        unit = RepairUnit(
            id="test::process::STATEMENT_BLOCK::3-4",
            file_path="proc.py",
            symbol="process",
            unit_type=RepairUnitType.STATEMENT_BLOCK,
            node_type="If",
            start_line=3,
            end_line=4,
            source_text="        if item > 0:\n            yield item\n",
            indentation="        ",
        )
        out = StructuredRepairOutput(
            repair_action="replace_block",
            target_unit_id=unit.id,
            replacement="if item >= 0:\n    yield item * 2",
        )
        rec = SourceReconstructor.reconstruct_single(code, unit, out, "proc.py")
        self.assertTrue(rec.success)
        val = StaticRepairValidator.validate({"proc.py": code}, rec, [unit])
        self.assertTrue(val.valid)

    def test_insert_statement(self):
        code = (
            "def calculate(a, b):\n"
            "    return a / b\n"
        )
        unit = RepairUnit(
            id="test::calculate::INSERT::2",
            file_path="calc.py",
            symbol="calculate",
            unit_type=RepairUnitType.STATEMENT,
            node_type="Return",
            start_line=2,
            end_line=2,
            indentation="    ",
        )
        out = StructuredRepairOutput(
            repair_action="insert_statement",
            target_unit_id=unit.id,
            replacement="if b == 0:\n    return 0",
        )
        rec = SourceReconstructor.reconstruct_single(code, unit, out, "calc.py")
        self.assertTrue(rec.success)
        self.assertIn("    if b == 0:\n        return 0\n    return a / b\n", rec.modified_contents["calc.py"])
        val = StaticRepairValidator.validate({"calc.py": code}, rec, [unit])
        self.assertTrue(val.valid)

    def test_multi_site_non_overlapping(self):
        code = (
            "def foo():\n"
            "    a = 1\n"
            "    b = 2\n"
            "    return a + b\n"
        )
        u1 = RepairUnit(
            id="u1",
            file_path="m.py",
            symbol="foo",
            unit_type=RepairUnitType.STATEMENT,
            node_type="Assign",
            start_line=2,
            end_line=2,
            indentation="    ",
        )
        u2 = RepairUnit(
            id="u2",
            file_path="m.py",
            symbol="foo",
            unit_type=RepairUnitType.STATEMENT,
            node_type="Assign",
            start_line=3,
            end_line=3,
            indentation="    ",
        )
        o1 = StructuredRepairOutput(repair_action="replace_statement", target_unit_id="u1", replacement="a = 10")
        o2 = StructuredRepairOutput(repair_action="replace_statement", target_unit_id="u2", replacement="b = 20")

        rec = SourceReconstructor.reconstruct_multi({"m.py": code}, [(u1, o1), (u2, o2)])
        self.assertTrue(rec.success)
        self.assertIn("a = 10", rec.modified_contents["m.py"])
        self.assertIn("b = 20", rec.modified_contents["m.py"])

    def test_overlapping_edits_rejected(self):
        code = "line 1\nline 2\nline 3\nline 4\n"
        u1 = RepairUnit(
            id="u1", file_path="t.py", symbol="t", unit_type=RepairUnitType.STATEMENT_BLOCK,
            node_type="Stmt", start_line=2, end_line=3,
        )
        u2 = RepairUnit(
            id="u2", file_path="t.py", symbol="t", unit_type=RepairUnitType.STATEMENT_BLOCK,
            node_type="Stmt", start_line=3, end_line=4,
        )
        o1 = StructuredRepairOutput(repair_action="replace_block", target_unit_id="u1", replacement="rep1")
        o2 = StructuredRepairOutput(repair_action="replace_block", target_unit_id="u2", replacement="rep2")

        rec = SourceReconstructor.reconstruct_multi({"t.py": code}, [(u1, o1), (u2, o2)])
        self.assertFalse(rec.success)
        self.assertIn("overlapping", rec.error.lower())

    def test_syntax_error_rejected_by_validator(self):
        code = "def f():\n    return 1\n"
        unit = RepairUnit(
            id="u1", file_path="f.py", symbol="f", unit_type=RepairUnitType.STATEMENT,
            node_type="Return", start_line=2, end_line=2, indentation="    ",
        )
        out = StructuredRepairOutput(
            repair_action="replace_statement", target_unit_id="u1",
            replacement="return def (invalid syntax here!",
        )
        rec = SourceReconstructor.reconstruct_single(code, unit, out, "f.py")
        self.assertTrue(rec.success)  # Diff is created
        val = StaticRepairValidator.validate({"f.py": code}, rec, [unit])
        self.assertFalse(val.valid)
        self.assertFalse(val.syntax_ok)
        self.assertIn("Syntax error", val.errors[0])

    def test_markdown_leakage_rejected_by_validator(self):
        code = "def f():\n    return 1\n"
        unit = RepairUnit(
            id="u1", file_path="f.py", symbol="f", unit_type=RepairUnitType.STATEMENT,
            node_type="Return", start_line=2, end_line=2, indentation="    ",
        )
        out = StructuredRepairOutput(
            repair_action="replace_statement", target_unit_id="u1",
            replacement="```python\nreturn 2\n```",
        )
        rec = SourceReconstructor.reconstruct_single(code, unit, out, "f.py")
        val = StaticRepairValidator.validate({"f.py": code}, rec, [unit])
        self.assertFalse(val.valid)
        self.assertFalse(val.no_markdown_leakage)

    def test_overexpansion_rejected_by_validator(self):
        code = "def f():\n    return 1\n"
        unit = RepairUnit(
            id="u1", file_path="f.py", symbol="f", unit_type=RepairUnitType.STATEMENT,
            node_type="Return", start_line=2, end_line=2, indentation="    ",
        )
        huge_replacement = "\n".join([f"x_{i} = {i}" for i in range(250)])
        out = StructuredRepairOutput(
            repair_action="replace_statement", target_unit_id="u1", replacement=huge_replacement
        )
        rec = SourceReconstructor.reconstruct_single(code, unit, out, "f.py")
        val = StaticRepairValidator.validate({"f.py": code}, rec, [unit], max_line_expansion=100)
        self.assertFalse(val.valid)
        self.assertFalse(val.line_expansion_ok)
        self.assertIn("expansion exceeded", val.errors[0])


if __name__ == "__main__":
    unittest.main()
