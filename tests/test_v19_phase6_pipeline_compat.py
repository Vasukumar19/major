"""Phase 6 Regression Tests: End-to-End Pipeline Compatibility.

Validates:
1. Ingestion of external work items (GitHub, Direct REST API) through normalization
   into Problem domain objects and ingestion into V1RepairOrchestrator.
2. Preservation of external metadata (ticket IDs, reporter info, labels, correlation ID).
3. Secret containment: No authentication credentials leaked in work items or Problem objects.
4. Seamless cooperation of all V1.9 repaired components:
   - Contract gate counterfactual preservation (Phase 1)
   - Diagnosis-orchestrator hypothesis and site consistency (Phase 2)
   - Verification classifier & replanner import defect handling (Phase 3)
   - Candidate profile file-qualification and ranking collision prevention (Phase 4).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import pytest

from patchforge.core.target import EditSite
from patchforge.diagnosis.candidate_analysis import CandidateProfile
from patchforge.diagnosis.contract import APIContract
from patchforge.diagnosis.contract_gate import HypothesisContractGate
from patchforge.diagnosis.diagnosis import CompetingDiagnosisResult, DiagnosisHypothesis
from patchforge.integrations.direct import DirectWorkItemIngestionHandler
from patchforge.integrations.github import GitHubWebhookHandler
from patchforge.integrations.work_item import PatchForgeWorkItem
from patchforge.repair.patch import Patch
from patchforge.repair.ranking import RepairSiteRanker
from patchforge.repair.replanner import FailureDrivenReplanner, ReplanAction
from patchforge.verification.classifier import FailureClass, classify


class DummyEvalResult:
    def __init__(
        self,
        resolved: bool = False,
        patch_applied: bool = True,
        fail_to_pass_passed: int = 0,
        fail_to_pass_total: int = 1,
        pass_to_pass_passed: int = 10,
        pass_to_pass_total: int = 10,
        infra_failure: bool = False,
        error: str = "",
        test_output: str = "",
        stderr: str = "",
    ):
        self.resolved = resolved
        self.patch_applied = patch_applied
        self.fail_to_pass_passed = fail_to_pass_passed
        self.fail_to_pass_total = fail_to_pass_total
        self.pass_to_pass_passed = pass_to_pass_passed
        self.pass_to_pass_total = pass_to_pass_total
        self.infra_failure = infra_failure
        self.error = error
        self.test_output = test_output
        self.stderr = stderr


def test_github_webhook_to_orchestrator_problem_flow():
    secret = "gh-secret-xyz"
    handler = GitHubWebhookHandler(webhook_secret=secret)

    payload_dict = {
        "action": "opened",
        "repository": {"full_name": "psf/requests", "default_branch": "v2.25.0"},
        "issue": {
            "number": 2674,
            "title": "iter_lines splits prematurely",
            "body": "When delimiter is set, iter_lines fails.\nReproduction: `pytest tests/test_lines.py`",
            "labels": [{"name": "bug"}, {"name": "streaming"}],
        },
        "sender": {"login": "dev_user"},
    }
    payload_bytes = json.dumps(payload_dict).encode("utf-8")
    sig = "sha256=" + hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()

    headers = {
        "X-GitHub-Event": "issues",
        "X-GitHub-Delivery": "gh-deliv-2674",
        "X-Hub-Signature-256": sig,
    }

    ok, status, msg, work_item = handler.handle_payload(payload_bytes, headers)
    assert ok is True
    assert work_item is not None

    # Metadata preservation
    assert work_item.issue_id == "GH-2674"
    assert work_item.metadata["sender"] == "dev_user"
    assert "bug" in work_item.labels

    # Problem conversion
    problem = work_item.to_problem()
    assert problem.instance_id == "GH-2674"
    assert problem.repo == "psf/requests"
    assert problem.base_commit == "v2.25.0"
    assert problem.fail_to_pass == ["pytest tests/test_lines.py"]
    assert "iter_lines splits prematurely" in problem.problem_statement


def test_direct_api_to_orchestrator_problem_flow():
    api_key = "direct-token-abc"
    handler = DirectWorkItemIngestionHandler(api_key=api_key)

    payload = {
        "repository": "pallets/flask",
        "issue_id": "FLASK-4992",
        "title": "Config.from_file mode parameter ignored",
        "problem_statement": "Passing mode='b' crashes in from_file.",
        "base_commit": "2.2.x",
        "reproduction_test": "tests/test_config.py::test_from_file_binary",
        "labels": ["config", "p1"],
        "metadata": {"reporter": "alice", "priority": "high", "environment": "production"},
    }
    payload_bytes = json.dumps(payload).encode("utf-8")

    ok, status, msg, work_item = handler.handle_request(payload_bytes, {"Authorization": f"Bearer {api_key}"})
    assert ok is True
    assert work_item is not None
    assert work_item.metadata["reporter"] == "alice"
    assert work_item.metadata["priority"] == "high"

    problem = work_item.to_problem()
    assert problem.instance_id == "FLASK-4992"
    assert problem.repo == "pallets/flask"
    assert problem.fail_to_pass == ["tests/test_config.py::test_from_file_binary"]


def test_secret_containment():
    api_key = "super-secret-key-999"
    handler = DirectWorkItemIngestionHandler(api_key=api_key)

    payload = {
        "repository": "owner/repo",
        "problem_statement": "Bug description",
        "metadata": {"auth_token": "leak_candidate"},
    }
    # Deliberately check that handler does not inject API key into item or problem
    ok, status, msg, work_item = handler.handle_request(json.dumps(payload).encode("utf-8"), {"X-API-Key": api_key})
    assert ok is True
    assert work_item is not None

    item_dict = work_item.to_dict()
    assert api_key not in json.dumps(item_dict)

    problem = work_item.to_problem()
    assert api_key not in json.dumps(problem.to_dict())


def test_cooperative_v19_pipeline_flow():
    """Validates the full cooperative interaction between all 4 V1.9 fixes:

    1. Phase 1: Contract gate preserves upstream winner.
    2. Phase 2: Hypothesis consistency synchronizes selected hypothesis & affected sites.
    3. Phase 4: Candidate profiles and site ranker use file-qualified identity.
    4. Phase 3: Classifier & replanner properly refine implementation defects.
    """
    # 1. Setup competing hypotheses with upstream winner
    h0 = DiagnosisHypothesis(
        id="hyp-iter_lines",
        cause="iter_lines buffer chunk boundary split failure",
        repair_strategy="Fix delimiter splitting in iter_lines",
        affected_sites=["requests/models.py:Response.iter_lines"],
        confidence=0.75,
        counterfactual_score=0.92,
    )
    h1 = DiagnosisHypothesis(
        id="hyp-other",
        cause="other site",
        repair_strategy="Modify Session.send",
        affected_sites=["requests/sessions.py:Session.send"],
        confidence=0.88,
        counterfactual_score=0.40,
    )
    diag = CompetingDiagnosisResult(
        hypotheses=[h0, h1],
        defect_confidence=0.80,
        selected_hypothesis_idx=0,
    )

    contract = APIContract(target_symbol="Response.iter_lines")
    # Contract gate evaluation (Phase 1)
    verified_diag = HypothesisContractGate.evaluate_and_filter(diag, contract)
    # Upstream winner h0 must be preserved
    assert verified_diag.selected_hypothesis_idx == 0
    assert verified_diag.selected_hypothesis == h0

    # Synchronization & active site derivation (Phase 2)
    assert len(verified_diag.affected_sites) == 1
    assert verified_diag.affected_sites[0] == "requests/models.py:Response.iter_lines"

    # Profile building and ranking with qualified keys (Phase 4)
    class DummyCounterfactual:
        def __init__(self, verdict: str):
            self.verdict = verdict

    site_models = EditSite(file_path="requests/models.py", line_start=750, line_end=765, symbol="Response.iter_lines")
    site_sessions = EditSite(file_path="requests/sessions.py", line_start=100, line_end=120, symbol="Session.send")
    site_test = EditSite(file_path="tests/test_models.py", line_start=50, line_end=60, symbol="Response.iter_lines")

    profiles = {
        "requests/models.py:Response.iter_lines": CandidateProfile(
            symbol="Response.iter_lines",
            file_path="requests/models.py",
            start_line=750,
            end_line=765,
            counterfactual=DummyCounterfactual("HIGHLY_PLAUSIBLE"),
        ),
        "requests/sessions.py:Session.send": CandidateProfile(
            symbol="Session.send",
            file_path="requests/sessions.py",
            start_line=100,
            end_line=120,
            counterfactual=DummyCounterfactual("WEAK"),
        ),
        "tests/test_models.py:Response.iter_lines": CandidateProfile(
            symbol="Response.iter_lines",
            file_path="tests/test_models.py",
            start_line=50,
            end_line=60,
            counterfactual=DummyCounterfactual("WEAK"),
        ),
    }

    ranker = RepairSiteRanker(graph=None)
    ranked = ranker.rank_sites(
        candidates=[site_test, site_sessions, site_models],
        candidate_profiles=profiles,
        diagnosis=verified_diag,
    )

    # models.py must be ranked first due to qualified diagnosis match and profile score
    assert ranked[0].file_path == "requests/models.py"
    assert ranked[0].symbol == "Response.iter_lines"

    # Replanner and classifier loop (Phase 3)
    patch = Patch(valid=True, patch_text="diff --git a/requests/models.py", files_changed=["requests/models.py"])
    eval_res = DummyEvalResult(
        fail_to_pass_passed=0,
        fail_to_pass_total=1,
        test_output="NameError: name 're_split' is not defined\n  File 'requests/models.py', line 755, in iter_lines",
    )
    f_class = classify(patch, eval_res, target_symbol="iter_lines", target_file="requests/models.py")
    assert f_class == FailureClass.PATCH_SEMANTICS

    decision = FailureDrivenReplanner.decide(
        failure_class=f_class.value,
        attempt=1,
        max_attempts=3,
        hypotheses_available=2,
        active_hypothesis_idx=0,
        f2p_passed=0,
        p2p_failed=0,
        error_message="NameError: name 're_split' is not defined",
    )
    # Must refine semantics with symbol guidance, keeping current hypothesis
    assert decision.action == ReplanAction.REFINE_SEMANTICS
    assert decision.target_hypothesis_id is None
    assert "UNRESOLVED SYMBOL/IMPORT" in decision.prompt_guidance
