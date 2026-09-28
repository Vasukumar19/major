"""Tests for PatchForge V1.1 Contract-Grounded Diagnosis and Hypothesis Selection."""
import ast
from patchforge.diagnosis.contract import APIContract, APIParameter, ContractExtractor
from patchforge.diagnosis.contract_gate import (
    BehavioralRequirement,
    BehavioralRequirementMatrix,
    HypothesisContractGate,
)
from patchforge.diagnosis.diagnosis import CompetingDiagnosisResult, DiagnosisHypothesis
from patchforge.repair.schema import ReconstructedPatch, RepairUnit, RepairUnitType
from patchforge.repair.validator import StaticRepairValidator


def test_contract_extractor_extracts_parameters_and_calls():
    problem_text = """
    app.config.from_file("config.toml", load=toml.load, text=False) raises TypeError
    """
    hints = "I think a text=True parameter would be better, easier to use True or False rather than mode strings"

    contract = ContractExtractor.extract_contract(
        problem_statement=problem_text,
        hints_text=hints,
        target_symbol="from_file",
    )

    assert contract.target_symbol == "from_file"
    param_names = [p.name for p in contract.parameters]
    assert "text" in param_names
    text_param = contract.get_parameter("text")
    assert text_param is not None
    assert text_param.type_annotation == "bool"
    assert text_param.default_value == "True"
    assert len(contract.raw_call_examples) >= 1
    assert "from_file" in contract.raw_call_examples[0]


def test_behavioral_requirement_matrix_decomposition_and_coverage():
    contract = APIContract(
        target_symbol="from_file",
        parameters=[APIParameter(name="text", default_value="True", origin="TEST_OBSERVED")],
        handled_exceptions=["IOError"],
    )
    matrix = BehavioralRequirementMatrix.decompose(
        problem_statement="Support binary loading in toml files",
        hints_text="Catch IOError",
        contract=contract,
    )

    assert len(matrix.requirements) >= 3
    # Check coverage on a patch that implements text parameter and binary mode
    patch_with_text = """
    def from_file(self, filename: str, load: Callable, silent: bool = False, text: bool = True) -> bool:
        mode = "r" if text else "rb"
        with open(filename, mode) as f:
            obj = load(f)
    """
    coverage = matrix.check_coverage(patch_with_text)
    # R1 (text parameter) and R_binary (mode/rb/text) should be satisfied
    assert any(cov is True for cov in coverage.values())


def test_hypothesis_contract_gate_eliminates_mode_in_favor_of_text():
    contract = APIContract(
        target_symbol="from_file",
        parameters=[APIParameter(name="text", default_value="True", origin="TEST_OBSERVED")],
    )

    h_mode = DiagnosisHypothesis(
        id="H1_MODE",
        cause="Missing mode argument for binary file opening.",
        repair_strategy="Add mode: str = 't' parameter to from_file to choose text vs binary.",
        confidence=0.85,
    )
    h_text = DiagnosisHypothesis(
        id="H2_TEXT",
        cause="Missing text parameter for binary TOML loading.",
        repair_strategy="Add text: bool = True parameter to from_file to open file in 'r' or 'rb'.",
        confidence=0.80,
    )

    diag = CompetingDiagnosisResult(
        hypotheses=[h_mode, h_text],
        selected_hypothesis_idx=0,
    )

    filtered = HypothesisContractGate.evaluate_and_filter(diag, contract)

    # H1_MODE must be eliminated because it introduces 'mode' instead of contract-required 'text'
    assert h_mode.eliminated is True
    assert "Contract Violation" in h_mode.elimination_reason
    # H2_TEXT must be selected as winner
    assert filtered.selected_hypothesis_idx == 1
    assert filtered.selected_hypothesis.id == "H2_TEXT"
    assert filtered.repair_strategy == h_text.repair_strategy


def test_static_repair_validator_catches_signature_mismatch():
    contract = APIContract(
        target_symbol="from_file",
        parameters=[APIParameter(name="text", default_value="True", origin="TEST_OBSERVED")],
    )

    original_code = """
def from_file(self, filename, load, silent=False):
    pass
"""
    # Bad patch: added mode instead of text
    bad_modified = """
def from_file(self, filename, load, silent=False, mode='r'):
    pass
"""
    bad_reconstructed = ReconstructedPatch(
        success=True,
        files_changed=["flask/config.py"],
        symbols_changed=["from_file"],
        modified_contents={"flask/config.py": bad_modified},
        patch_text="fake diff",
    )
    unit = RepairUnit(
        id="unit-1",
        node_type="FunctionDef",
        unit_type=RepairUnitType.FUNCTION,
        file_path="flask/config.py",
        symbol="from_file",
        start_line=1,
        end_line=3,
        source_text=original_code,
    )

    val_res_bad = StaticRepairValidator.validate(
        original_sources={"flask/config.py": original_code},
        reconstructed=bad_reconstructed,
        target_units=[unit],
        contract=contract,
    )
    assert val_res_bad.valid is False
    assert any("Contract Signature Violation" in err for err in val_res_bad.errors)

    # Good patch: added text: bool = True
    good_modified = """
def from_file(self, filename, load, silent=False, text=True):
    pass
"""
    good_reconstructed = ReconstructedPatch(
        success=True,
        files_changed=["flask/config.py"],
        symbols_changed=["from_file"],
        modified_contents={"flask/config.py": good_modified},
        patch_text="fake diff",
    )

    val_res_good = StaticRepairValidator.validate(
        original_sources={"flask/config.py": original_code},
        reconstructed=good_reconstructed,
        target_units=[unit],
        contract=contract,
    )
    assert val_res_good.valid is True
    assert len(val_res_good.errors) == 0


def test_hypothesis_contract_gate_synthesizes_when_all_violate_contract():
    contract = APIContract(
        target_symbol="from_file",
        parameters=[APIParameter(name="text", type_annotation="bool", default_value="True", origin="HINTS_TEXT")],
        forbidden_parameters=["mode"],
    )

    h_mode1 = DiagnosisHypothesis(
        id="H1",
        cause="Binary loading needs mode",
        repair_strategy="Add mode parameter with default 'r'",
        confidence=0.9,
    )
    h_mode2 = DiagnosisHypothesis(
        id="H2",
        cause="Mode parameter required",
        repair_strategy="Add mode: str = 'b' argument",
        confidence=0.85,
    )

    diag = CompetingDiagnosisResult(
        hypotheses=[h_mode1, h_mode2],
        selected_hypothesis_idx=0,
        repair_sites=["flask/config.py:from_file"],
    )

    filtered = HypothesisContractGate.evaluate_and_filter(diag, contract)

    # Both mode hypotheses must be eliminated
    assert h_mode1.eliminated is True
    assert h_mode2.eliminated is True

    # A contract-grounded hypothesis must be synthesized and selected
    assert filtered.selected_hypothesis.id == "H_CONTRACT_GROUNDED"
    assert "text: bool = True" in filtered.selected_hypothesis.repair_strategy
    assert filtered.selected_hypothesis_idx == 2

