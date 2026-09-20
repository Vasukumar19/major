"""Unit tests for RepairUnitPlanner."""
import unittest

from patchforge.repair.planner import RepairUnitPlanner
from patchforge.repair.schema import RepairUnitType


class TestRepairPlanner(unittest.TestCase):

    def test_plan_statement_unit(self):
        code = (
            "def compute(a, b):\n"
            "    c = a + b\n"
            "    return c\n"
        )
        unit = RepairUnitPlanner.plan_repair_unit(
            file_path="math_utils.py",
            source_code=code,
            target_symbol="compute",
            target_lines=(2, 2),
        )
        self.assertEqual(unit.file_path, "math_utils.py")
        self.assertEqual(unit.symbol, "compute")
        self.assertEqual(unit.start_line, 2)
        self.assertEqual(unit.end_line, 2)
        self.assertEqual(unit.unit_type, RepairUnitType.STATEMENT)
        self.assertEqual(unit.node_type, "Assign")
        self.assertEqual(unit.indentation, "    ")
        self.assertIn("c = a + b", unit.source_text)

    def test_plan_statement_block_unit(self):
        code = (
            "def handler(req):\n"
            "    if req.is_redirect:\n"
            "        req = req.copy()\n"
            "        req.url = new_url\n"
            "    return req\n"
        )
        unit = RepairUnitPlanner.plan_repair_unit(
            file_path="server.py",
            source_code=code,
            target_symbol="handler",
            target_lines=(2, 4),
        )
        self.assertEqual(unit.start_line, 2)
        self.assertEqual(unit.end_line, 4)
        self.assertEqual(unit.unit_type, RepairUnitType.STATEMENT_BLOCK)
        self.assertEqual(unit.node_type, "If")
        self.assertEqual(unit.indentation, "    ")

    def test_plan_function_unit(self):
        code = (
            "class App:\n"
            "    def handle(self, msg):\n"
            "        print(msg)\n"
            "        return True\n"
        )
        unit = RepairUnitPlanner.plan_repair_unit(
            file_path="app.py",
            source_code=code,
            target_symbol="handle",
            target_lines=(2, 4),
        )
        self.assertEqual(unit.symbol, "handle")
        self.assertEqual(unit.unit_type, RepairUnitType.METHOD)
        self.assertEqual(unit.parent_symbol, "App")
        self.assertEqual(unit.start_line, 2)
        self.assertEqual(unit.end_line, 4)

    def test_plan_expression_unit_when_preferred(self):
        code = (
            "def check(expr):\n"
            "    result = cached_eval(expr)\n"
            "    return result\n"
        )
        unit = RepairUnitPlanner.plan_repair_unit(
            file_path="eval.py",
            source_code=code,
            target_symbol="check",
            target_lines=(2, 2),
            preferred_granularity=RepairUnitType.EXPRESSION,
        )
        self.assertEqual(unit.unit_type, RepairUnitType.EXPRESSION)
        self.assertEqual(unit.node_type, "Call")
        self.assertEqual(unit.source_text, "cached_eval(expr)")

    def test_fallback_on_unparseable_source(self):
        bad_code = "def bad_syntax(:\n    return\n"
        unit = RepairUnitPlanner.plan_repair_unit(
            file_path="bad.py",
            source_code=bad_code,
            target_symbol="bad_syntax",
            target_lines=(1, 2),
        )
        self.assertEqual(unit.unit_type, RepairUnitType.STATEMENT_BLOCK)
        self.assertEqual(unit.start_line, 1)


if __name__ == "__main__":
    unittest.main()
