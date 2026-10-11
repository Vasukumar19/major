"""Tests for V1.5 assertion-directed completeness and repair reliability enhancements."""
import pytest
from patchforge.repair.schema import (
    RepairAction,
    RepairUnit,
    RepairUnitType,
    SchemaValidationResult,
    StructuredRepairOutput,
)
from patchforge.repair.structured_repair import StructuredRepairParser
from patchforge.repair.replanner import FailureDrivenReplanner, ReplanAction
from patchforge.repair.reconstructor import SourceReconstructor
from patchforge.verification.classifier import FailureClass


def test_reject_noop_identical_replacement():
    """Verify that StructuredRepairParser rejects replacements identical to the target source."""
    orig = "def foo():\n    return 42\n"
    unit = RepairUnit(
        id="test::foo",
        file_path="foo.py",
        symbol="foo",
        unit_type=RepairUnitType.FUNCTION,
        node_type="FunctionDef",
        start_line=1,
        end_line=2,
        source_text=orig,
    )
    out = StructuredRepairOutput(
        repair_action=RepairAction.REPLACE_METHOD.value,
        target_unit_id="test::foo",
        target_symbol="foo",
        replacement=orig,
        schema_validation=SchemaValidationResult(valid=True),
    )
    sem_res = StructuredRepairParser.validate_semantic_repair(out, unit)
    assert not sem_res.valid
    assert any("no-op repair" in err for err in sem_res.errors)


def test_reject_function_without_def():
    """Verify that replacing a FUNCTION/METHOD with random statements without def is caught."""
    orig = "def foo():\n    return 42\n"
    unit = RepairUnit(
        id="test::foo",
        file_path="foo.py",
        symbol="foo",
        unit_type=RepairUnitType.FUNCTION,
        node_type="FunctionDef",
        start_line=1,
        end_line=2,
        source_text=orig,
    )
    out = StructuredRepairOutput(
        repair_action=RepairAction.REPLACE_METHOD.value,
        target_unit_id="test::foo",
        target_symbol="foo",
        replacement="x = 10\ny = 20\n",
        schema_validation=SchemaValidationResult(valid=True),
    )
    sem_res = StructuredRepairParser.validate_semantic_repair(out, unit)
    assert not sem_res.valid
    assert any("does not contain a valid function definition" in err for err in sem_res.errors)


def test_replanner_handles_unresolved_and_noop_failures():
    """Verify FailureDrivenReplanner gives explicit modification guidance on UNRESOLVED."""
    decision = FailureDrivenReplanner.decide(
        failure_class=FailureClass.UNRESOLVED.value,
        attempt=1,
        max_attempts=3,
        error_message="No changes produced between original source and reconstructed source.",
    )
    assert decision.action == ReplanAction.REFINE_SEMANTICS
    assert "produced no modifications" in decision.reason
    assert "MUST introduce the necessary bug fix logic" in decision.prompt_guidance


def test_auto_heal_compat_imports():
    """Verify that reconstructor automatically heals unimported to_native_string from compat."""
    orig_code = '''from .compat import is_py2, str\n\ndef request(method):\n    return method\n'''
    new_code = '''from .compat import is_py2, str\n\ndef request(method):\n    method = to_native_string(method)\n    return method\n'''
    healed = SourceReconstructor._auto_heal_missing_imports(orig_code, new_code)
    assert "to_native_string" in healed
    assert "from .compat import to_native_string" in healed or "to_native_string" in healed
