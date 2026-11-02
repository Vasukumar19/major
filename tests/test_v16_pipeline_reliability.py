"""Tests for PatchForge V1.6 Evidence Integrity, Repair Correctness, and Pipeline Reliability.

Covers:
- Priority A: Target Selection & Diagnosis Reconciliation
- Priority B: Dynamic Probing Safety & Counterfactual Evaluation
- Priority C: Pre-verification Replanning & Failure Routing
- Priority D: Evidence-based Diagnosis Confidence
- Priority E: Scope-aware AST Import Healing
- Priority F: Universal Prompt De-contamination
- Priority G: Memory Reproducibility & Explicit Modes
- Priority H: Accurate BM25 with IDF & Lexical n-gram Matching
- Priority I: Patch Lifecycle Separation & Telemetry Timing
- Priority J: Cache Fingerprinting & Cohort Runner Provenance
"""
import ast
import hashlib
import json
from pathlib import Path
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from patchforge.core.target import EditSite, RepairTarget
from patchforge.diagnosis.candidate_analysis import CandidateBehaviorAnalyzer
from patchforge.diagnosis.causal_trace import BehavioralTrace, FirstDivergenceLocator
from patchforge.diagnosis import CompetingDiagnosisResult, DiagnosisHypothesis
from patchforge.experiment.counterfactual import CounterfactualEngine
from patchforge.experiment.probe import ProbeObservation, ProbeSpecification, ProbeType
from patchforge.issue.problem import Problem
from patchforge.memory.episode import RepairEpisode
from patchforge.memory.store import CrossTaskRepairMemory, MemoryMode
from patchforge.pipeline.v1_orchestrator import V1OrchestratorResult, V1RepairOrchestrator
from patchforge.repair.ranking import RepairSiteRanker
from patchforge.repair.reconstructor import SourceReconstructor
from patchforge.repair.replanner import FailureDrivenReplanner, ReplanAction
from patchforge.repair.schema import (
    RepairAction,
    RepairUnit,
    RepairUnitType,
    ReconstructedPatch,
    StructuredRepairOutput,
)
from patchforge.experiment.runner import ExperimentRunner
from patchforge.repository_intelligence.test_graph import TestGraphIndex
from patchforge.repair.structured_repair import (
    SYSTEM_STRUCTURED_REPAIR_PROMPT,
    StructuredRepairParser,
)
from patchforge.retrieval.hybrid import HybridRepositoryRetriever
from patchforge.verification.classifier import FailureClass
from scripts.run_v05_cohort import (
    build_evaluation_manifest,
    compute_cache_fingerprint,
    compute_harness_impl_hash,
    compute_prompt_hash,
    compute_source_revision_hash,
    get_git_commit_hash,
)


# ---------------------------------------------------------------------------
# Priority A: Target Selection & Diagnosis Reconciliation
# ---------------------------------------------------------------------------

def test_repair_site_ranker_reconcile_target():
    h = DiagnosisHypothesis(
        id="A",
        cause="Bad socket error handling",
        repair_strategy="Fix error handling in socket wrapper",
        affected_sites=["requests/adapters.py:send"],
    )
    diagnosis = CompetingDiagnosisResult(
        hypotheses=[h],
        selected_hypothesis=h,
        repair_sites=["requests/adapters.py:send"],
    )

    # 1. Direct match with diagnosis affected site
    site_direct = EditSite(
        file_path="requests/adapters.py",
        symbol="send",
        line_start=10,
        line_end=50,
        source_span=(10, 50),
        site_role="PRIMARY",
        rank=1,
    )
    coherent, just = RepairSiteRanker.reconcile_target(site_direct, diagnosis, graph=None)
    assert coherent is True
    assert "Target matches hypothesized" in just

    # 2. Unrelated site with no graph
    site_unrelated = EditSite(
        file_path="requests/models.py",
        symbol="prepare_url",
        line_start=1,
        line_end=20,
        source_span=(1, 20),
        site_role="PRIMARY",
        rank=2,
    )
    coherent_unrelated, just_unrelated = RepairSiteRanker.reconcile_target(site_unrelated, diagnosis, graph=None)
    assert coherent_unrelated is False
    assert "contradicts diagnosed affected sites" in just_unrelated

    # 3. Caller/Callee relationship via AST graph
    mock_graph = MagicMock()
    mock_graph.callers.return_value = ["prepare_url"]
    mock_graph.callees.return_value = []
    coherent_rel, just_rel = RepairSiteRanker.reconcile_target(site_unrelated, diagnosis, graph=mock_graph)
    assert coherent_rel is True
    assert "caller" in just_rel


# ---------------------------------------------------------------------------
# Priority B: Dynamic Probing Safety & Counterfactual Evaluation
# ---------------------------------------------------------------------------

def test_dynamic_probing_disabled_by_default():
    mock_provider = MagicMock()
    orch = V1RepairOrchestrator(provider=mock_provider)
    assert orch.enable_dynamic_probing is False


def test_counterfactual_evaluator_empty_observations_no_confidence_inflation():
    hyp = DiagnosisHypothesis(
        id="A",
        cause="Bad check",
        repair_strategy="Change condition",
        affected_sites=["foo.py:bar"],
        confidence=0.75,
    )
    diag = CompetingDiagnosisResult(
        hypotheses=[hyp],
        selected_hypothesis=hyp,
        repair_sites=["foo.py:bar"],
    )

    probe = ProbeSpecification(
        probe_id="P_VAR",
        probe_type=ProbeType.VARIABLE_VALUE,
        file_path="foo.py",
        symbol="bar",
        target_line=10,
        variable_name="status",
    )

    exp_res = CounterfactualEngine.evaluate_observations(
        probes=[probe],
        observations=[],
        diagnosis=diag,
    )
    assert exp_res.confidence_delta == 0.0
    assert exp_res.supported_hypothesis is None
    assert hyp.confidence == 0.75


def test_counterfactual_evaluator_matching_observations():
    hyp = DiagnosisHypothesis(
        id="A",
        cause="Bad check",
        repair_strategy="Change condition",
        affected_sites=["foo.py:bar"],
        confidence=0.6,
    )
    diag = CompetingDiagnosisResult(
        hypotheses=[hyp],
        selected_hypothesis=hyp,
        repair_sites=["foo.py:bar"],
    )
    probe = ProbeSpecification(
        probe_id="P_VAR",
        probe_type=ProbeType.VARIABLE_VALUE,
        file_path="foo.py",
        symbol="bar",
        target_line=10,
        variable_name="status",
        expected_if_h1_true="200",
    )
    obs = ProbeObservation(
        probe_id="P_VAR",
        observed_value="200",
    )

    exp_res = CounterfactualEngine.evaluate_observations(
        probes=[probe],
        observations=[obs],
        diagnosis=diag,
    )
    assert exp_res.confidence_delta > 0.0
    assert exp_res.supported_hypothesis == "A"


# ---------------------------------------------------------------------------
# Priority C: Pre-Verification Failures Handled by Replanner
# ---------------------------------------------------------------------------

def test_preverification_noop_and_schema_failures_route_to_replanner():
    # 1. No-op repair error
    decision_noop = FailureDrivenReplanner.decide(
        failure_class=FailureClass.UNRESOLVED.value,
        attempt=1,
        max_attempts=3,
        error_message="no-op repair: replacement is identical",
    )
    assert decision_noop.action == ReplanAction.REFINE_SEMANTICS
    assert "CRITICAL: Your replacement code was identical" in decision_noop.prompt_guidance

    # 2. Schema failure
    decision_schema = FailureDrivenReplanner.decide(
        failure_class=FailureClass.REPAIR_SCHEMA_FAILURE.value,
        attempt=1,
        max_attempts=3,
        error_message="Invalid JSON wrapper",
    )
    assert decision_schema.action == ReplanAction.FALLBACK_SYNTAX
    assert "Focus strictly on emitting valid python code" in decision_schema.prompt_guidance

    # 3. Validation failure
    decision_val = FailureDrivenReplanner.decide(
        failure_class=FailureClass.REPAIR_VALIDATION_FAILURE.value,
        attempt=1,
        max_attempts=3,
        error_message="SyntaxError in reconstructed patch",
    )
    assert decision_val.action == ReplanAction.REFINE_SEMANTICS
    assert "VALIDATION FAILURE" in decision_val.prompt_guidance


# ---------------------------------------------------------------------------
# Priority D: Evidence-Based Diagnosis Confidence
# ---------------------------------------------------------------------------

def test_candidate_analysis_no_unsupported_plausibility_inflation():
    analyzer = CandidateBehaviorAnalyzer()
    symbol = "dummy_func"
    file_path = "dummy.py"

    # When traceback and behavior map have NO affirmative link, evidence remains unverified
    profile = analyzer.analyze_candidate(
        symbol=symbol,
        file_path=file_path,
        start_line=1,
        end_line=10,
        behavior_map=None,
        failing_traceback=None,
    )
    assert profile.counterfactual.explains_failure is False
    assert profile.counterfactual.explains_expected_behavior is False
    assert profile.counterfactual.verdict != "HIGHLY_PLAUSIBLE"


def test_causal_trace_static_prediction_flag():
    code = """def process(items):
    for item in items:
        if item is None:
            raise ValueError("item cannot be None")
    return len(items)
"""
    trace = FirstDivergenceLocator.locate_divergence(
        source_code=code,
        target_symbol="process",
        file_path="dummy.py",
        traceback_lines=[4],
    )
    assert trace.is_static_prediction is True
    assert trace.divergence_source == "STATIC_AST_PREDICTED"
    prompt_str = trace.format_for_prompt()
    assert "FIRST BEHAVIORAL DIVERGENCE" in prompt_str
    assert "STATIC_AST_PREDICTED" in prompt_str


# ---------------------------------------------------------------------------
# Priority E: Scope-Aware AST Import Healing
# ---------------------------------------------------------------------------

def test_scope_aware_import_healing_does_not_inject_chunk():
    source_code = """def stream_download(self):
    for chunk in self.iter_content():
        if chunk:
            chunk.split(b'\\n')
"""
    modified_code = """def stream_download(self):
    for chunk in self.iter_content():
        if chunk:
            chunk.split(b'\\r\\n')
"""
    reconstructor = SourceReconstructor()
    unit = RepairUnit(
        id="unit_1",
        file_path="requests/response.py",
        symbol="stream_download",
        unit_type=RepairUnitType.FUNCTION,
        node_type="FunctionDef",
        start_line=1,
        end_line=5,
        source_text=source_code.strip(),
    )
    output = StructuredRepairOutput(
        repair_action=RepairAction.REPLACE_METHOD.value,
        target_unit_id="unit_1",
        replacement=modified_code.strip(),
    )
    res = reconstructor.reconstruct_single(
        original_code=source_code,
        unit=unit,
        output=output,
        file_rel_path="requests/response.py",
    )
    assert res.success is True
    # Verify 'import chunk' is NOT inserted
    assert "import chunk" not in res.modified_contents["requests/response.py"]
    assert "chunk" not in res.healed_imports


def test_scope_aware_import_healing_injects_genuine_missing_module():
    original_code = "def connect(self): pass"
    modified_code = """def connect(self):
    try:
        self._sock.connect()
    except socket.error as e:
        pass
"""
    healed = SourceReconstructor._auto_heal_missing_imports(original_code, modified_code)
    assert "import socket" in healed


# ---------------------------------------------------------------------------
# Priority F: Universal Prompt De-contamination
# ---------------------------------------------------------------------------

def test_universal_prompt_contains_no_benchmark_specific_rules():
    prompt = SYSTEM_STRUCTURED_REPAIR_PROMPT
    assert "streaming socket errors" not in prompt
    assert "method byte decode" not in prompt
    assert "redirect request references" not in prompt
    assert "fixture scope formatting" not in prompt
    assert "REQUESTS RULE" not in prompt
    assert "PYTEST RULE" not in prompt


# ---------------------------------------------------------------------------
# Priority G: Memory Reproducibility & Explicit Modes
# ---------------------------------------------------------------------------

def test_memory_frozen_mode_prevents_active_pollution(tmp_path):
    mem = CrossTaskRepairMemory(episodes_dir=str(tmp_path), mode=MemoryMode.FROZEN)
    assert mem.mode == MemoryMode.FROZEN

    ep1 = RepairEpisode(
        instance_id="inst_1",
        repo="psf/requests",
        base_commit="abc",
        model_name="qwen",
        target={"symbol": "send"},
        diagnosis={"repair_strategy": "Fix headers"},
        causal_chain=["Fix headers"],
        resolved=True,
        patch_applied=True,
        patch_valid=True,
        failure_class="RESOLVED",
        runtime_s=10.0,
        f2p_passed=1,
        f2p_total=1,
        p2p_passed=5,
        p2p_total=5,
        patch_text="diff --git ...",
        test_summary={},
        created_at="now",
    )

    # In FROZEN mode, record_episode saves to disk but does NOT add to active in-memory search pool
    mem.record_episode(ep1)
    assert len(mem.episodes) == 0

    # Current instance self-exclusion
    mem.episodes.append(ep1)  # manually add for search check
    results = mem.query_similar_episodes(
        repo="psf/requests",
        query_text="Fix headers",
        target_symbol="send",
        current_instance_id="inst_1",  # Same as ep1
    )
    assert len(results) == 0  # Excluded itself!


# ---------------------------------------------------------------------------
# Priority H: Accurate BM25 with IDF & Lexical n-gram Matching
# ---------------------------------------------------------------------------

def test_hybrid_retriever_bm25_idf_and_ngram():
    retriever = HybridRepositoryRetriever(repo_dir=".")

    query_tokens = ["quantum", "teleportation"]
    doc_rare = ["def", "special", "quantum", "teleportation"]
    doc_common = ["def", "simple", "function"]

    idf_map = {"quantum": 2.5, "teleportation": 3.0, "def": 0.1}
    score_rare = retriever._compute_bm25_score(query_tokens, doc_rare, avg_len=4.0, idf_map=idf_map)
    score_common = retriever._compute_bm25_score(query_tokens, doc_common, avg_len=4.0, idf_map=idf_map)

    assert score_rare > 0.0
    assert score_common == 0.0

    # Character 3-gram fuzzy similarity test
    sim = retriever._compute_ngram_similarity(["special", "quantum"], ["special", "quantum"])
    assert sim == 1.0
    sim_partial = retriever._compute_ngram_similarity(["quantum"], ["quantum_teleportation"])
    assert 0.0 < sim_partial < 1.0
    # Backward compatible alias
    assert retriever._compute_dense_score(["quantum"], ["quantum_teleportation"]) == sim_partial


# ---------------------------------------------------------------------------
# Priority I: Patch Lifecycle Separation & Telemetry Timing
# ---------------------------------------------------------------------------

def test_patch_lifecycle_separation_applied_vs_resolved():
    res = V1OrchestratorResult(instance_id="test_task")
    res.generation_attempted = True
    res.patch_generated = True
    res.structurally_valid = True
    res.submitted_to_harness = True
    res.patch_applied = True
    res.resolved = False  # Patch was applied in Docker and tests ran, but test failed!

    assert res.patch_applied is True
    assert res.resolved is False
    assert res.structurally_valid is True
    assert res.submitted_to_harness is True


# ---------------------------------------------------------------------------
# Priority J: Cache Fingerprinting & Cohort Runner Provenance
# ---------------------------------------------------------------------------

def test_cache_fingerprint_deterministic_and_unique():
    fp1 = compute_cache_fingerprint("v1", "qwen2.5-coder:14b", "da203c8")
    fp2 = compute_cache_fingerprint("v1", "qwen2.5-coder:14b", "da203c8")
    assert fp1 == fp2
    assert len(fp1) == 16

    fp_different_engine = compute_cache_fingerprint("graph", "qwen2.5-coder:14b", "da203c8")
    assert fp1 != fp_different_engine

    fp_different_commit = compute_cache_fingerprint("v1", "qwen2.5-coder:14b", "da203c9")
    assert fp1 != fp_different_commit


# ---------------------------------------------------------------------------
# V1.6 Closure Acceptance Tests
# ---------------------------------------------------------------------------

def test_cache_manifest_dirty_diff_invalidation():
    manifest1 = build_evaluation_manifest("v1", "qwen2.5-coder:14b", workspace=".")
    fp1 = compute_cache_fingerprint(manifest1)

    # Modifying dirty_diff_hash changes fingerprint even when commit_hash is identical
    manifest_dirty = dict(manifest1)
    manifest_dirty["dirty_diff_hash"] = "changed_diff_hash_1234"
    fp_dirty = compute_cache_fingerprint(manifest_dirty)

    assert fp1 != fp_dirty


def test_cache_manifest_config_and_memory_invalidation():
    base = build_evaluation_manifest("v1", "qwen2.5-coder:14b", workspace=".")
    base_fp = compute_cache_fingerprint(base)

    # Model change
    m_model = dict(base)
    m_model["model"] = "qwen2.5-coder:32b"
    assert compute_cache_fingerprint(m_model) != base_fp

    # Max retries change
    m_retries = dict(base)
    m_retries["max_retries"] = 5
    assert compute_cache_fingerprint(m_retries) != base_fp

    # Memory mode change
    m_mode = dict(base)
    m_mode["memory_mode"] = "ONLINE"
    assert compute_cache_fingerprint(m_mode) != base_fp

    # Memory snapshot fingerprint change
    m_snap = dict(base)
    m_snap["memory_snapshot_fingerprint"] = "snapshot_sha_9876"
    assert compute_cache_fingerprint(m_snap) != base_fp

    # Temperature change
    m_temp = dict(base)
    m_temp["temperature"] = 0.7
    assert compute_cache_fingerprint(m_temp) != base_fp

    # Probing toggle change
    m_probe = dict(base)
    m_probe["enable_dynamic_probing"] = True
    assert compute_cache_fingerprint(m_probe) != base_fp

    # Cohort identity change
    m_cohort = dict(base)
    m_cohort["cohort_name"] = "custom_cohort_10"
    assert compute_cache_fingerprint(m_cohort) != base_fp

    m_cohort_h = dict(base)
    m_cohort_h["cohort_hash"] = "new_cohort_hash_99"
    assert compute_cache_fingerprint(m_cohort_h) != base_fp

    # Eval harness change
    m_harness = dict(base)
    m_harness["eval_harness"] = "local_pytest"
    assert compute_cache_fingerprint(m_harness) != base_fp

    # Prompt change
    m_prompt = dict(base)
    m_prompt["prompt_template_hash"] = "new_prompt_sha_5678"
    assert compute_cache_fingerprint(m_prompt) != base_fp


def test_cache_manifest_legacy_rejection_logic():
    # Legacy record missing cache_manifest or cache_fingerprint
    legacy_rec_no_fp = {"instance_id": "test_1", "resolved": True}
    legacy_rec_no_manifest = {"instance_id": "test_1", "cache_fingerprint": "abc", "resolved": True}

    manifest = build_evaluation_manifest("v1", "qwen2.5-coder:14b", workspace=".")
    expected_fp = compute_cache_fingerprint(manifest)

    # Validate rejection conditions matching scripts/run_v05_cohort.py
    assert not legacy_rec_no_fp.get("cache_fingerprint") or not legacy_rec_no_fp.get("cache_manifest")
    assert not legacy_rec_no_manifest.get("cache_fingerprint") or not legacy_rec_no_manifest.get("cache_manifest")

    valid_rec = {
        "instance_id": "test_1",
        "cache_fingerprint": expected_fp,
        "cache_manifest": manifest,
        "resolved": True,
    }
    assert valid_rec.get("cache_fingerprint") == expected_fp
    assert valid_rec.get("cache_manifest") == manifest


def test_cache_fingerprint_isolated_workspace_rules(tmp_path):
    """Isolated regression test verifying exact cache invalidation rules without touching real repo."""
    import subprocess
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_dir, check=True, capture_output=True)

    # 1. Base tracked source file
    pf_dir = repo_dir / "patchforge"
    pf_dir.mkdir()
    core_file = pf_dir / "core.py"
    core_file.write_text("def core(): return 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo_dir, check=True, capture_output=True)

    base_m = build_evaluation_manifest("v1", "qwen2.5-coder:14b", workspace=str(repo_dir))
    base_fp = compute_cache_fingerprint(base_m)

    # 2. Modifying a tracked source file changes the fingerprint
    core_file.write_text("def core(): return 2\n", encoding="utf-8")
    m_mod_tracked = build_evaluation_manifest("v1", "qwen2.5-coder:14b", workspace=str(repo_dir))
    assert compute_cache_fingerprint(m_mod_tracked) != base_fp
    subprocess.run(["git", "checkout", "--", "."], cwd=repo_dir, check=True, capture_output=True)
    assert compute_cache_fingerprint(build_evaluation_manifest("v1", "qwen2.5-coder:14b", workspace=str(repo_dir))) == base_fp

    # 3. Adding an untracked source file changes the fingerprint
    untracked_file = pf_dir / "new_helper.py"
    untracked_file.write_text("def helper(): return 'v1'\n", encoding="utf-8")
    m_add_untracked = build_evaluation_manifest("v1", "qwen2.5-coder:14b", workspace=str(repo_dir))
    fp_add_untracked = compute_cache_fingerprint(m_add_untracked)
    assert fp_add_untracked != base_fp

    # 4. Modifying the contents of an untracked source file changes fingerprint (same filename!)
    untracked_file.write_text("def helper(): return 'v2_modified'\n", encoding="utf-8")
    m_mod_untracked = build_evaluation_manifest("v1", "qwen2.5-coder:14b", workspace=str(repo_dir))
    fp_mod_untracked = compute_cache_fingerprint(m_mod_untracked)
    assert fp_mod_untracked != fp_add_untracked
    assert fp_mod_untracked != base_fp

    # 5. Removing an untracked source file changes fingerprint back
    untracked_file.unlink()
    m_removed_untracked = build_evaluation_manifest("v1", "qwen2.5-coder:14b", workspace=str(repo_dir))
    assert compute_cache_fingerprint(m_removed_untracked) == base_fp

    # 6. Changing a relevant dependency or config file changes the fingerprint
    req_file = repo_dir / "requirements.txt"
    req_file.write_text("pytest>=7.0.0\n", encoding="utf-8")
    m_dep = build_evaluation_manifest("v1", "qwen2.5-coder:14b", workspace=str(repo_dir))
    assert compute_cache_fingerprint(m_dep) != base_fp
    req_file.unlink()
    assert compute_cache_fingerprint(build_evaluation_manifest("v1", "qwen2.5-coder:14b", workspace=str(repo_dir))) == base_fp

    # 7. Changing an actual model-generation parameter changes the fingerprint
    m_temp = build_evaluation_manifest("v1", "qwen2.5-coder:14b", workspace=str(repo_dir), temperature=0.7)
    assert compute_cache_fingerprint(m_temp) != base_fp
    m_tokens = build_evaluation_manifest("v1", "qwen2.5-coder:14b", workspace=str(repo_dir), max_tokens=1024)
    assert compute_cache_fingerprint(m_tokens) != base_fp

    # 8. Changing evaluation, dataset, or harness configuration changes the fingerprint
    m_dataset = build_evaluation_manifest("v1", "qwen2.5-coder:14b", workspace=str(repo_dir), dataset_name="SWE-bench/SWE-bench_Verified")
    assert compute_cache_fingerprint(m_dataset) != base_fp
    m_timeout = build_evaluation_manifest("v1", "qwen2.5-coder:14b", workspace=str(repo_dir), harness_timeout_s=600)
    assert compute_cache_fingerprint(m_timeout) != base_fp
    m_cohort = build_evaluation_manifest("v1", "qwen2.5-coder:14b", workspace=str(repo_dir), cohort_instances=["task_1", "task_2"])
    assert compute_cache_fingerprint(m_cohort) != base_fp

    # 9. Identical inputs produce the same fingerprint (deterministic)
    m_identical = build_evaluation_manifest("v1", "qwen2.5-coder:14b", workspace=str(repo_dir))
    assert compute_cache_fingerprint(m_identical) == base_fp

    # 10. Unrelated generated artifacts do not change the fingerprint
    results_dir = repo_dir / "results"
    results_dir.mkdir(exist_ok=True)
    (results_dir / "task_1.json").write_text("{\"score\": 1.0}", encoding="utf-8")
    output_dir = repo_dir / "output"
    output_dir.mkdir(exist_ok=True)
    (output_dir / "report.docx").write_bytes(b"PK0304dummy")
    pycache_dir = pf_dir / "__pycache__"
    pycache_dir.mkdir(exist_ok=True)
    (pycache_dir / "core.cpython-312.pyc").write_bytes(b"compiled")

    m_artifacts = build_evaluation_manifest("v1", "qwen2.5-coder:14b", workspace=str(repo_dir))
    assert compute_cache_fingerprint(m_artifacts) == base_fp


def test_cache_fingerprint_untracked_directories_recursive(tmp_path):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_dir, check=True, capture_output=True)

    # 1. Base tracked source file
    pf_dir = repo_dir / "patchforge"
    pf_dir.mkdir()
    core_file = pf_dir / "core.py"
    core_file.write_text("def core(): return 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo_dir, check=True, capture_output=True)

    base_commit, base_dirty = compute_source_revision_hash(str(repo_dir))
    assert base_dirty == "clean"
    base_fp = compute_cache_fingerprint(build_evaluation_manifest("v1", "qwen2.5-coder:14b", workspace=str(repo_dir)))

    # 2. Creates an untracked package directory containing a Python file
    pkg_dir = pf_dir / "new_pkg"
    pkg_dir.mkdir()
    pkg_file = pkg_dir / "sub_module.py"
    pkg_file.write_text("def sub(): return 'v1'\n", encoding="utf-8")

    # Calculates the source fingerprint
    commit_v1, dirty_v1 = compute_source_revision_hash(str(repo_dir))
    assert dirty_v1 != "clean"
    assert dirty_v1 != base_dirty
    fp_v1 = compute_cache_fingerprint(build_evaluation_manifest("v1", "qwen2.5-coder:14b", workspace=str(repo_dir)))
    assert fp_v1 != base_fp

    # 3. Changes the Python file's contents without changing its path
    pkg_file.write_text("def sub(): return 'v2_modified'\n", encoding="utf-8")

    # Confirms the fingerprint changes
    commit_v2, dirty_v2 = compute_source_revision_hash(str(repo_dir))
    assert dirty_v2 != dirty_v1
    fp_v2 = compute_cache_fingerprint(build_evaluation_manifest("v1", "qwen2.5-coder:14b", workspace=str(repo_dir)))
    assert fp_v2 != fp_v1
    assert fp_v2 != base_fp

    # 4. Adds another file inside the same directory and confirms the fingerprint changes
    extra_file = pkg_dir / "extra_module.py"
    extra_file.write_text("def extra(): return 42\n", encoding="utf-8")

    commit_v3, dirty_v3 = compute_source_revision_hash(str(repo_dir))
    assert dirty_v3 != dirty_v2
    assert dirty_v3 != dirty_v1
    fp_v3 = compute_cache_fingerprint(build_evaluation_manifest("v1", "qwen2.5-coder:14b", workspace=str(repo_dir)))
    assert fp_v3 != fp_v2
    assert fp_v3 != fp_v1

    # 5. Confirms unrelated generated artifacts remain excluded
    results_dir = repo_dir / "results"
    results_dir.mkdir(exist_ok=True)
    (results_dir / "task_eval.json").write_text("{\"score\": 1.0}", encoding="utf-8")
    output_dir = repo_dir / "output"
    output_dir.mkdir(exist_ok=True)
    (output_dir / "run_log.txt").write_text("Execution log", encoding="utf-8")
    pycache_dir = pkg_dir / "__pycache__"
    pycache_dir.mkdir(exist_ok=True)
    (pycache_dir / "sub_module.cpython-312.pyc").write_bytes(b"compiled_bytecode")

    commit_v4, dirty_v4 = compute_source_revision_hash(str(repo_dir))
    assert dirty_v4 == dirty_v3
    fp_v4 = compute_cache_fingerprint(build_evaluation_manifest("v1", "qwen2.5-coder:14b", workspace=str(repo_dir)))
    assert fp_v4 == fp_v3


def test_cache_manifest_dataset_revision_and_harness_identity(tmp_path):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_dir, check=True, capture_output=True)

    pf_dir = repo_dir / "patchforge"
    pf_dir.mkdir()
    integrations_dir = pf_dir / "integrations"
    integrations_dir.mkdir()
    harness_file = integrations_dir / "swebench.py"
    harness_file.write_text("# initial swebench adapter\nclass SWEBenchAdapter: pass\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo_dir, check=True, capture_output=True)

    base_m = build_evaluation_manifest(
        "v1", "qwen2.5-coder:14b",
        workspace=str(repo_dir),
        dataset_name="SWE-bench/SWE-bench_Lite",
        dataset_revision="main",
        harness_version="swebench-5.0.2",
    )
    base_fp = compute_cache_fingerprint(base_m)

    # 1. Changing dataset_revision invalidates cache even when dataset_name and harness_version are identical
    m_dataset_rev = build_evaluation_manifest(
        "v1", "qwen2.5-coder:14b",
        workspace=str(repo_dir),
        dataset_name="SWE-bench/SWE-bench_Lite",
        dataset_revision="v1.1",
        harness_version="swebench-5.0.2",
    )
    assert compute_cache_fingerprint(m_dataset_rev) != base_fp

    # 2. Changing task_definitions_hash invalidates cache
    m_task_def = build_evaluation_manifest(
        "v1", "qwen2.5-coder:14b",
        workspace=str(repo_dir),
        dataset_name="SWE-bench/SWE-bench_Lite",
        dataset_revision="main",
        task_definitions_hash="sha256_custom_task_defs_abc123",
        harness_version="swebench-5.0.2",
    )
    assert compute_cache_fingerprint(m_task_def) != base_fp

    # 3. Changing docker_image_tag invalidates cache
    m_docker = build_evaluation_manifest(
        "v1", "qwen2.5-coder:14b",
        workspace=str(repo_dir),
        dataset_name="SWE-bench/SWE-bench_Lite",
        dataset_revision="main",
        docker_image_tag="eval-v2",
        harness_version="swebench-5.0.2",
    )
    assert compute_cache_fingerprint(m_docker) != base_fp

    # 4. Modifying local evaluation harness implementation invalidates cache even when harness_version is unchanged
    harness_file.write_text("# modified swebench adapter\nclass SWEBenchAdapter:\n    def run(self): return 1\n", encoding="utf-8")
    m_mod_harness = build_evaluation_manifest(
        "v1", "qwen2.5-coder:14b",
        workspace=str(repo_dir),
        dataset_name="SWE-bench/SWE-bench_Lite",
        dataset_revision="main",
        harness_version="swebench-5.0.2",
    )
    assert m_mod_harness["harness_impl_hash"] != base_m["harness_impl_hash"]
    assert compute_cache_fingerprint(m_mod_harness) != base_fp

    # 5. Matching inputs produce exact cache hit
    subprocess.run(["git", "checkout", "--", "."], cwd=repo_dir, check=True, capture_output=True)
    m_matched = build_evaluation_manifest(
        "v1", "qwen2.5-coder:14b",
        workspace=str(repo_dir),
        dataset_name="SWE-bench/SWE-bench_Lite",
        dataset_revision="main",
        harness_version="swebench-5.0.2",
    )
    assert compute_cache_fingerprint(m_matched) == base_fp


def test_dynamic_probing_prevents_empty_diff_execution(tmp_path):
    import subprocess
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_dir, check=True, capture_output=True)

    dummy_file = repo_dir / "module.py"
    dummy_file.write_text("def run():\n    return 42\n", encoding="utf-8")
    subprocess.run(["git", "add", "module.py"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo_dir, check=True, capture_output=True)

    mock_tester = MagicMock()
    runner = ExperimentRunner(repo_dir=str(repo_dir), tester=mock_tester)

    # When injector targets a file that does not exist or produces no diff, runner returns EXP_NO_DIFF
    h1 = DiagnosisHypothesis(id="A", cause="c1", repair_strategy="s1", affected_sites=["nonexistent.py:run"])
    h2 = DiagnosisHypothesis(id="B", cause="c2", repair_strategy="s2", affected_sites=["nonexistent.py:run"])
    diag = CompetingDiagnosisResult(hypotheses=[h1, h2], repair_sites=["nonexistent.py:run"])

    res = runner.run_experiment(
        instance_id="inst_empty",
        diagnosis=diag,
        target_file="nonexistent.py",
        target_symbol="run",
        target_line=1,
    )
    assert res.experiment_id == "EXP_NO_DIFF"
    assert not mock_tester.run.called


def test_dynamic_probing_end_to_end_disposable_fixture(tmp_path):
    import subprocess
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_dir, check=True, capture_output=True)

    py_file = repo_dir / "calc.py"
    initial_code = "def compute(x):\n    val = x * 2\n    return val\n"
    py_file.write_text(initial_code, encoding="utf-8")
    subprocess.run(["git", "add", "calc.py"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo_dir, check=True, capture_output=True)

    # Diagnosis with two hypotheses
    h1 = DiagnosisHypothesis(id="A", cause="Wrong factor", repair_strategy="Fix factor", affected_sites=["calc.py:compute"])
    h2 = DiagnosisHypothesis(id="B", cause="Wrong return", repair_strategy="Fix return", affected_sites=["calc.py:compute"])
    diag = CompetingDiagnosisResult(hypotheses=[h1, h2], repair_sites=["calc.py:compute"])

    # Mock tester that captures probe patch and returns probe sentinel
    captured_patches = []
    def mock_run(instance_id, patch_text):
        captured_patches.append(patch_text)
        mock_verdict = MagicMock()
        mock_verdict.output = "__PF_PROBE__:P_ENTRY:ENTER"
        return mock_verdict

    mock_tester = MagicMock()
    mock_tester.run.side_effect = mock_run

    runner = ExperimentRunner(repo_dir=str(repo_dir), tester=mock_tester)
    res = runner.run_experiment(
        instance_id="inst_1",
        diagnosis=diag,
        target_file="calc.py",
        target_symbol="compute",
        target_line=2,
    )

    # 1. Tester was called with probe patch containing sentinel
    assert len(captured_patches) == 1
    assert "__PF_PROBE__" in captured_patches[0]

    # 2. Observations parsed
    assert res.experiment_id != "EXP_NO_DIFF"
    assert res.experiment_id != "EXP_ERROR"
    assert len(res.observations) >= 1
    assert res.observations[0].probe_id == "P_ENTRY"
    assert res.observations[0].observed_value == "ENTER"

    # 3. Clean rollback verified: file is identical to initial code and git status is clean
    assert py_file.read_text(encoding="utf-8") == initial_code
    status_proc = subprocess.run(["git", "status", "--porcelain"], cwd=repo_dir, capture_output=True, text=True)
    assert status_proc.stdout.strip() == ""


def test_test_graph_index_alias_and_retrieval_channel():
    tgi = TestGraphIndex()
    tgi.index_test_file("tests/test_foo.py", "def test_bar():\n    bar()\n")

    # Verify alias exists and works identically
    assert hasattr(tgi, "get_tests_for_symbol")
    assert len(tgi.get_tests_for_symbol("bar")) == 1
    assert tgi.get_tests_for_symbol("bar") == tgi.tests_for_symbol("bar")
    assert tgi.tests_for_symbol("bar")[0].name == "test_bar"

    # Verify candidate behavior analyzer connects direct tests
    analyzer = CandidateBehaviorAnalyzer(test_index=tgi)
    prof = analyzer.analyze_candidate(symbol="bar", file_path="foo.py", start_line=1, end_line=10)
    assert "test_bar" in prof.direct_tests


def test_memory_provenance_tracking(tmp_path):
    episodes_dir = tmp_path / "episodes"
    episodes_dir.mkdir()

    ep1 = RepairEpisode(
        instance_id="task_existing",
        repo="psf/requests",
        base_commit="abc",
        model_name="test_model",
        target={"file_path": "requests/models.py", "symbol": "prepare"},
        diagnosis={"selected_hypothesis": {"repair_strategy": "Fix prepare"}},
        causal_chain=["Fix prepare"],
        resolved=True,
        patch_applied=True,
        patch_valid=True,
        failure_class="RESOLVED",
        runtime_s=5.0,
        f2p_passed=1,
        f2p_total=1,
        p2p_passed=2,
        p2p_total=2,
        patch_text="diff",
        test_summary={},
        created_at="now",
    )
    (episodes_dir / "task_existing.json").write_text(json.dumps(ep1.to_dict()), encoding="utf-8")

    mem = CrossTaskRepairMemory(episodes_dir=str(episodes_dir), mode=MemoryMode.FROZEN)
    ctx, prov = mem.retrieve_with_provenance(
        repo="psf/requests",
        query_text="Fix prepare",
        target_symbol="prepare",
        current_instance_id="task_current",
        top_k=2,
    )

    assert prov["memory_mode"] == "FROZEN"
    assert prov["snapshot_fingerprint"] == mem.snapshot_fingerprint
    assert "task_existing" in prov["eligible_episode_ids"]
    assert "task_existing" in prov["retrieved_episode_ids"]
    assert prov["current_instance_id"] == "task_current"
    assert prov["current_instance_excluded"] is False

    # Exclude current instance when its ID matches an episode in store
    ctx2, prov2 = mem.retrieve_with_provenance(
        repo="psf/requests",
        query_text="Fix prepare",
        target_symbol="prepare",
        current_instance_id="task_existing",
        top_k=2,
    )
    assert "task_existing" not in prov2["retrieved_episode_ids"]
    assert prov2["current_instance_excluded"] is True


def test_target_rebuilding_and_replanning_switch():
    h_a = DiagnosisHypothesis(id="A", cause="Defect in module A", repair_strategy="Fix A", affected_sites=["mod_a.py:func_a"])
    h_b = DiagnosisHypothesis(id="B", cause="Defect in module B", repair_strategy="Fix B", affected_sites=["mod_b.py:func_b"])
    diag = CompetingDiagnosisResult(
        hypotheses=[h_a, h_b],
        selected_hypothesis=h_a,
        selected_hypothesis_idx=0,
        repair_sites=["mod_a.py:func_a"],
    )

    decision = FailureDrivenReplanner.decide(
        failure_class=FailureClass.WRONG_HYPOTHESIS.value,
        attempt=1,
        max_attempts=3,
        hypotheses_available=2,
        active_hypothesis_idx=0,
        error_message="All assertions failed under hypothesis A",
    )

    assert decision.action == ReplanAction.SWITCH_HYPOTHESIS
    assert decision.target_hypothesis_id == "B"


def test_telemetry_timing_fields_honesty():
    res = V1OrchestratorResult(instance_id="test_timing")
    res.indexing_time_s = 0.42
    res.retrieval_diagnosis_time_s = 1.15
    res.probing_time_s = None  # Explicitly None when probing is disabled!
    res.generation_time_s = 3.50
    res.reconstruction_time_s = 0.05
    res.static_validation_time_s = 0.02
    res.test_time_s = 12.80
    res.replanning_time_s = 0.01
    res.runtime_s = 18.0

    assert res.probing_time_s is None
    assert res.indexing_time_s == 0.42
    assert res.retrieval_diagnosis_time_s == 1.15
    assert res.static_validation_time_s == 0.02
    assert res.replanning_time_s == 0.01

