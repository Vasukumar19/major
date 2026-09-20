"""Comprehensive test suite for PatchForge v0.6.1 Structured Repair Reliability Protocol."""
from __future__ import annotations

import json
from pathlib import Path
import pytest

from patchforge.repair.schema import (
    RepairAction,
    RepairUnit,
    RepairUnitType,
    SchemaValidationResult,
    SemanticValidationResult,
    UNIT_CANONICAL_ACTIONS,
    UNIT_PERMITTED_ACTIONS,
)
from patchforge.repair.structured_repair import (
    StructuredRepairParser,
    StructuredRepairPromptBuilder,
)


@pytest.fixture
def sample_method_unit() -> RepairUnit:
    return RepairUnit(
        id="requests/models.py::prepare::METHOD::236-251",
        file_path="requests/models.py",
        symbol="prepare",
        unit_type=RepairUnitType.METHOD,
        node_type="FunctionDef",
        start_line=236,
        end_line=251,
        source_text='    def prepare(self):\n        return self\n',
        parent_symbol="Request",
        indentation="    ",
    )


@pytest.fixture
def sample_expr_unit() -> RepairUnit:
    return RepairUnit(
        id="pkg/core.py::calc::EXPRESSION::10-10",
        file_path="pkg/core.py",
        symbol="calc",
        unit_type=RepairUnitType.EXPRESSION,
        node_type="Call",
        start_line=10,
        end_line=10,
        start_col=4,
        end_col=25,
        source_text="compute_val(x, y)",
        parent_symbol="calc",
        indentation="    ",
    )


@pytest.fixture
def sample_stmt_unit() -> RepairUnit:
    return RepairUnit(
        id="pkg/core.py::calc::STATEMENT::12-12",
        file_path="pkg/core.py",
        symbol="calc",
        unit_type=RepairUnitType.STATEMENT,
        node_type="Assign",
        start_line=12,
        end_line=12,
        source_text="result = a + b",
        parent_symbol="calc",
        indentation="    ",
    )


# --- 1. SCHEMA VALIDATION TESTS ---

def test_valid_json_in_markdown_fence(sample_method_unit):
    raw = '''```json
{
  "repair_action": "replace_method",
  "target_unit_id": "requests/models.py::prepare::METHOD::236-251",
  "replacement": "def prepare(self):\\n    return True",
  "reasoning": "Simple fix"
}
```'''
    out = StructuredRepairParser.parse(raw, sample_method_unit)
    assert out.repair_action == "replace_method"
    assert out.target_unit_id == sample_method_unit.id
    assert "def prepare(self):" in out.replacement
    assert out.schema_validation.valid is True


def test_unescaped_docstrings_in_replacement(sample_method_unit):
    """The critical bug in v0.6: model outputs unescaped triple quotes in docstring."""
    raw = '''```json
{
  "repair_action": "replace_method",
  "target_unit_id": "requests/models.py::prepare::METHOD::236-251",
  "target_symbol": "prepare",
  "replacement": "    def prepare(self):\\n        """Constructs a PreparedRequest."""\\n        return PreparedRequest()",
  "reasoning": "Wrapped in try/except.",
  "invariant": "All exceptions wrapped."
}
```'''
    out = StructuredRepairParser.parse(raw, sample_method_unit)
    assert out.repair_action == "replace_method"
    assert "Constructs a PreparedRequest." in out.replacement
    assert out.schema_validation.valid is True
    assert out.schema_validation.repaired is True


def test_unescaped_inner_quotes_and_dict(sample_method_unit):
    """Tests model replacement with inner quotes and dictionary literals."""
    raw = '''```json
{
  "repair_action": "replace_function",
  "target_unit_id": "requests/models.py::prepare::METHOD::236-251",
  "replacement": "def prepare(self):\\n    d = {'Content-Type': \\"application/json\\"}\\n    return d",
  "reasoning": "Add header dictionary"
}
```'''
    out = StructuredRepairParser.parse(raw, sample_method_unit)
    assert out.target_unit_id == sample_method_unit.id
    assert "Content-Type" in out.replacement


def test_think_tags_stripped(sample_method_unit):
    raw = '''<think>
We need to modify prepare() to wrap DecodeError.
Let's construct the replacement JSON.
</think>
```json
{
  "repair_action": "replace_method",
  "target_unit_id": "requests/models.py::prepare::METHOD::236-251",
  "replacement": "def prepare(self):\\n    pass"
}
```'''
    out = StructuredRepairParser.parse(raw, sample_method_unit)
    assert "<think>" not in out.replacement
    assert out.target_unit_id == sample_method_unit.id


def test_ambiguous_multiple_json_rejected(sample_method_unit):
    """Multiple candidate JSON objects must be rejected as AMBIGUOUS_REPAIR_OUTPUT."""
    raw = '''Here are two possible fixes:
Option 1:
```json
{
  "repair_action": "replace_method",
  "target_unit_id": "requests/models.py::prepare::METHOD::236-251",
  "replacement": "def prepare(self): return 1"
}
```
Option 2:
```json
{
  "repair_action": "replace_method",
  "target_unit_id": "requests/models.py::prepare::METHOD::236-251",
  "replacement": "def prepare(self): return 2"
}
```'''
    with pytest.raises(ValueError, match="AMBIGUOUS_REPAIR_OUTPUT"):
        StructuredRepairParser.parse(raw, sample_method_unit)


def test_action_auto_correction(sample_method_unit, sample_expr_unit):
    """Incompatible actions should be auto-corrected to canonical unit action."""
    # Method unit receiving replace_statement
    raw = '''```json
{
  "repair_action": "replace_statement",
  "target_unit_id": "requests/models.py::prepare::METHOD::236-251",
  "replacement": "def prepare(self):\\n    return True"
}
```'''
    out = StructuredRepairParser.parse(raw, sample_method_unit)
    # Automatically corrected to replace_method because STATEMENT is not permitted for METHOD
    assert out.repair_action == RepairAction.REPLACE_METHOD.value

    # Expression unit receiving replace_method
    raw_expr = '''```json
{
  "repair_action": "replace_method",
  "target_unit_id": "pkg/core.py::calc::EXPRESSION::10-10",
  "replacement": "compute_val(x, z)"
}
```'''
    out_expr = StructuredRepairParser.parse(raw_expr, sample_expr_unit)
    assert out_expr.repair_action == RepairAction.REPLACE_EXPRESSION.value


def test_empty_replacement_rejected(sample_method_unit):
    raw = '''```json
{
  "repair_action": "replace_method",
  "target_unit_id": "requests/models.py::prepare::METHOD::236-251",
  "replacement": ""
}
```'''
    with pytest.raises(ValueError, match="EMPTY_REPLACEMENT"):
        StructuredRepairParser.parse(raw, sample_method_unit)


# --- 2. LAYER B: SEMANTIC AST VALIDATION TESTS ---

def test_ast_category_expression_valid(sample_expr_unit):
    raw = '''```json
{
  "repair_action": "replace_expression",
  "target_unit_id": "pkg/core.py::calc::EXPRESSION::10-10",
  "replacement": "a + b * math.sqrt(c)"
}
```'''
    out = StructuredRepairParser.parse(raw, sample_expr_unit)
    assert out.semantic_validation.valid is True
    assert out.semantic_validation.ast_category_ok is True


def test_ast_category_expression_syntax_error(sample_expr_unit):
    raw = '''```json
{
  "repair_action": "replace_expression",
  "target_unit_id": "pkg/core.py::calc::EXPRESSION::10-10",
  "replacement": "a + * b"
}
```'''
    with pytest.raises(ValueError, match="SEMANTIC_VALIDATION_FAILURE"):
        StructuredRepairParser.parse(raw, sample_expr_unit)


def test_ast_category_statement_valid(sample_stmt_unit):
    raw = '''```json
{
  "repair_action": "replace_statement",
  "target_unit_id": "pkg/core.py::calc::STATEMENT::12-12",
  "replacement": "result = compute(x, y)"
}
```'''
    out = StructuredRepairParser.parse(raw, sample_stmt_unit)
    assert out.semantic_validation.valid is True


def test_diff_marker_leakage_rejected(sample_method_unit):
    raw = '''```json
{
  "repair_action": "replace_method",
  "target_unit_id": "requests/models.py::prepare::METHOD::236-251",
  "replacement": "<<<<<<< SEARCH\\ndef prepare():\\n=======\\ndef prepare():\\n>>>>>>> REPLACE"
}
```'''
    with pytest.raises(ValueError, match="SEMANTIC_VALIDATION_FAILURE"):
        StructuredRepairParser.parse(raw, sample_method_unit)


def test_python_code_block_fallback(sample_method_unit):
    """When model generates only Python code inside ```python ``` block."""
    raw = '''Here is the replacement code:
```python
def prepare(self):
    """Constructs a PreparedRequest."""
    return PreparedRequest()
```'''
    out = StructuredRepairParser.parse(raw, sample_method_unit)
    assert out.target_unit_id == sample_method_unit.id
    assert "Constructs a PreparedRequest." in out.replacement
    assert out.repair_action == "replace_method"


# --- 3. REPLAY REAL FAILED EPISODES FROM V0.6 ---

def test_replay_all_9_v06_schema_failures():
    """Replays all 9 actual raw model outputs recorded in v0.6 diagnostics."""
    diag_file = Path("scratch/schema_failure_diagnostics.json")
    if not diag_file.exists():
        pytest.skip("Diagnostics file not found")

    diagnostics = json.loads(diag_file.read_text(encoding="utf-8"))
    
    # Mapping of expected target units for each task
    targets = {
        "psf__requests-2674": RepairUnit(
            id="requests/models.py::prepare::METHOD::236-251",
            file_path="requests/models.py", symbol="prepare",
            unit_type=RepairUnitType.METHOD, node_type="FunctionDef",
            start_line=236, end_line=251, source_text="def prepare(self): pass",
        ),
        "psf__requests-2317": RepairUnit(
            id="requests/sessions.py::session::FUNCTION::668-671",
            file_path="requests/sessions.py", symbol="session",
            unit_type=RepairUnitType.FUNCTION, node_type="FunctionDef",
            start_line=668, end_line=671, source_text="def session(): pass",
        ),
        "pallets__flask-4992": RepairUnit(
            id="src/flask/config.py::from_file::METHOD::232-273",
            file_path="src/flask/config.py", symbol="from_file",
            unit_type=RepairUnitType.METHOD, node_type="FunctionDef",
            start_line=232, end_line=273, source_text="def from_file(self): pass",
        ),
        "pallets__flask-5063": RepairUnit(
            id="src/flask/json/tag.py::check::METHOD::70-72",
            file_path="src/flask/json/tag.py", symbol="check",
            unit_type=RepairUnitType.METHOD, node_type="FunctionDef",
            start_line=70, end_line=72, source_text="def check(self, value): pass",
        ),
        "pytest-dev__pytest-5692": RepairUnit(
            id="src/_pytest/outcomes.py::Skipped::STATEMENT_BLOCK::42-54",
            file_path="src/_pytest/outcomes.py", symbol="Skipped",
            unit_type=RepairUnitType.STATEMENT_BLOCK, node_type="ClassDef",
            start_line=42, end_line=54, source_text="class Skipped: pass",
        ),
        "pytest-dev__pytest-7220": RepairUnit(
            id="src/_pytest/python.py::_inject_setup_class_fixture::METHOD::689-711",
            file_path="src/_pytest/python.py", symbol="_inject_setup_class_fixture",
            unit_type=RepairUnitType.METHOD, node_type="FunctionDef",
            start_line=689, end_line=711, source_text="def _inject_setup_class_fixture(self): pass",
        ),
        "pytest-dev__pytest-7490": RepairUnit(
            id="src/_pytest/python.py::module::METHOD::265-268",
            file_path="src/_pytest/python.py", symbol="module",
            unit_type=RepairUnitType.METHOD, node_type="FunctionDef",
            start_line=265, end_line=268, source_text="def module(self): pass",
        ),
        "pylint-dev__pylint-7114": RepairUnit(
            id="pylint/config/argument.py::__init__::METHOD::165-187",
            file_path="pylint/config/argument.py", symbol="__init__",
            unit_type=RepairUnitType.METHOD, node_type="FunctionDef",
            start_line=165, end_line=187, source_text="def __init__(self): pass",
        ),
        "pylint-dev__pylint-7228": RepairUnit(
            id="pylint/config/argument.py::__init__::METHOD::156-178",
            file_path="pylint/config/argument.py", symbol="__init__",
            unit_type=RepairUnitType.METHOD, node_type="FunctionDef",
            start_line=156, end_line=178, source_text="def __init__(self): pass",
        ),
    }

    recovered_count = 0
    for inst_id, res in diagnostics.items():
        raw = res.get("raw", "")
        unit = targets.get(inst_id)
        assert unit is not None, f"Missing target for {inst_id}"
        out = StructuredRepairParser.parse(raw, unit)
        assert out is not None
        assert len(out.replacement) > 0
        assert out.target_unit_id == unit.id
        recovered_count += 1

    assert recovered_count == 9, f"Expected all 9 to be recovered, got {recovered_count}"
