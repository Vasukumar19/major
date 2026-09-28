"""Tests for PatchForge V1.2 Algorithmic Diagnosis & Execution-Path Reasoning."""
import ast
import pytest

from patchforge.diagnosis.contract import (
    APIContract,
    APIParameter,
    ContractApplicabilityClassifier,
    ContractCategory,
    ContractExtractor,
)
from patchforge.diagnosis.contract_gate import (
    BehavioralRequirement,
    BehavioralRequirementMatrix,
    HypothesisContractGate,
)
from patchforge.diagnosis.execution_path import (
    ExecutionPathBuilder,
    ExecutionPathModel,
    ExecutionPathNode,
)
from patchforge.repair.schema import ReconstructedPatch, RepairUnit, RepairUnitType
from patchforge.repair.validator import StaticRepairValidator


def test_contract_applicability_classification():
    # 1. API Signature case (Flask-4992 style)
    flask_issue = (
        "Flask url_for and send_file issue: add parameter text=True to dump_with_context "
        "def dump_with_context(obj, text: bool = True): kwargs text should be accepted."
    )
    cat, conf = ContractApplicabilityClassifier.classify(flask_issue, "`text=True` parameter")
    assert cat in (ContractCategory.API_SIGNATURE, ContractCategory.MIXED)
    assert conf >= 0.7

    contract = ContractExtractor.extract_contract(flask_issue, "`text=True` parameter", target_symbol="dump_with_context")
    assert contract.is_signature_contract is True
    param_names = [p.name for p in contract.parameters]
    assert "text" in param_names

    # 2. Algorithmic defect (Requests-2674 / general urllib3 / python version style)
    algo_issue = (
        "urllib3 decoding error on Response.content.\n"
        "When decoding content on Python 3.8, urllib3 raises DecodeError.\n"
        "Traceback (most recent call last):\n"
        "  File 'requests/models.py', line 740, in content\n"
        "    args to decode were invalid\n"
    )
    cat_algo, conf_algo = ContractApplicabilityClassifier.classify(algo_issue, "")
    assert cat_algo == ContractCategory.ALGORITHMIC_BEHAVIORAL
    assert conf_algo <= 0.5

    contract_algo = ContractExtractor.extract_contract(algo_issue, "", target_symbol="content")
    assert contract_algo.is_signature_contract is False
    # Ensure tokens like 'python', 'args', 'to', 'file', 'line' are NOT treated as required parameters
    bad_tokens = {"python", "args", "to", "file", "line"}
    extracted_names = {p.name for p in contract_algo.parameters}
    assert not (bad_tokens & extracted_names)


def test_static_repair_validator_selective_contract():
    unit = RepairUnit(
        id="requests/models.py:content",
        file_path="requests/models.py",
        symbol="content",
        unit_type=RepairUnitType.FUNCTION,
        node_type="FunctionDef",
        start_line=1,
        end_line=20,
    )
    original_code = "def content(self):\n    return self._content\n"
    reconstructed_code = "def content(self):\n    try:\n        return self._content\n    except Exception:\n        return b''\n"

    reconstructed = ReconstructedPatch(
        success=True,
        modified_contents={"requests/models.py": reconstructed_code},
        patch_text="--- a/requests/models.py\n+++ b/requests/models.py\n@@ -1,2 +1,5 @@\n",
        files_changed=["requests/models.py"],
        symbols_changed=["content"],
    )

    # When contract is ALGORITHMIC_BEHAVIORAL, it should NOT fail validation for missing parameters
    contract = APIContract(category=ContractCategory.ALGORITHMIC_BEHAVIORAL.value, confidence=0.2, target_symbol="content")
    val_res = StaticRepairValidator.validate(
        original_sources={"requests/models.py": original_code},
        reconstructed=reconstructed,
        target_units=[unit],
        contract=contract,
    )
    assert val_res.valid is True
    assert len(val_res.errors) == 0

    # When contract IS API_SIGNATURE and requires 'text', it should flag parameter absence
    sig_contract = APIContract(
        category=ContractCategory.API_SIGNATURE.value,
        confidence=0.9,
        target_symbol="content",
    )
    sig_contract.parameters.append(APIParameter(name="text", default_value="True"))
    assert sig_contract.is_signature_contract is True

    val_res_sig = StaticRepairValidator.validate(
        original_sources={"requests/models.py": original_code},
        reconstructed=reconstructed,
        target_units=[unit],
        contract=sig_contract,
    )
    assert val_res_sig.valid is False
    assert any("text" in err for err in val_res_sig.errors)


def test_execution_path_builder_and_divergence():
    sample_code = '''
def check_domain_scope(url, hostname):
    if not url:
        return False
    parts = url.split("://")
    host = parts[-1].split("/")[0]
    if host == hostname:
        return True
    if host.endswith("." + hostname):
        return True
    return False
'''
    # Test traceback line matching
    model = ExecutionPathBuilder.build_path(
        file_path="requests/utils.py",
        source_code=sample_code,
        target_symbol="check_domain_scope",
        traceback_lines=[8],
    )
    assert model.target_symbol == "check_domain_scope"
    assert len(model.nodes) > 0
    assert model.divergence_node is not None
    assert model.divergence_node.line_number == 8
    assert model.divergence_node.is_divergence_point is True

    summary = model.format_for_prompt()
    assert "EXECUTION PATH ANALYSIS" in summary
    assert ">>> [DIVERGENCE POINT] Line    8" in summary
    assert "Root Algorithmic Divergence" in summary


def test_requirement_matrix_enrichment():
    sample_code = '''
def resolve_redirect(req, resp):
    if resp.is_redirect:
        req.url = resp.headers['location']
    return req
'''
    exec_path = ExecutionPathBuilder.build_path(
        file_path="requests/sessions.py",
        source_code=sample_code,
        target_symbol="resolve_redirect",
        problem_statement="Redirect scope validation failing for subdomain dots",
    )

    matrix = BehavioralRequirementMatrix.decompose(
        problem_statement="Redirect scope validation failing for subdomain dots",
        hints_text="",
        contract=None,
        execution_path=exec_path,
    )
    assert any(r.category == "ALGORITHMIC_TRANSITION" for r in matrix.requirements)
    algo_req = next(r for r in matrix.requirements if r.category == "ALGORITHMIC_TRANSITION")
    assert "resolve_redirect" in algo_req.affected_symbols
    assert algo_req.source == "EXECUTION_PATH"

    # Test requirement evaluation
    test_patch = """
--- a/requests/sessions.py
+++ b/requests/sessions.py
@@ -2,2 +2,3 @@
     if resp.is_redirect:
+        # Handle subdomain scope
         req.url = resp.headers['location']
"""
    results = matrix.evaluate(test_patch)
    assert isinstance(results, dict)
