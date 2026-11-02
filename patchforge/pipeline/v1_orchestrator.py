"""PatchForge V1 Autonomous Software-Repair Orchestrator.

Integrates:
- Specification Extraction (IssueBehaviorExtractor)
- Repository Intelligence (AST Graph, Test Graph, Control/State Flow)
- Temporal Repository Graph (Git commits, churn, co-change frequencies)
- Hybrid Retrieval Layer (BM25, Dense, Graph, Temporal fusion)
- Unified Evidence Engine (Static, behavioral, runtime, temporal, probe substrate)
- Competing Hypotheses & Executable Counterfactuals
- Dynamic Sandboxed Probing Subsystem (Non-destructive AST instrumentation in Docker)
- Cross-Task Repair Memory (Historical episode recall)
- AST-Grounded Repair Unit Planning (Single & Multi-site)
- Structured AST Repair (Layer A/B validation with 3-tier fallback)
- Deterministic Source Reconstruction (Unified diff generation)
- Adaptive Tiered Verification (Static -> Targeted F2P -> Regression P2P)
- Failure-Driven Replanning
- Episode Persistence
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from patchforge.core.target import EditSite, MultiSiteRepairTarget, RepairTarget
from patchforge.diagnosis import (
    BehavioralEvidenceEngine,
    CandidateBehaviorAnalyzer,
    CandidateProfile,
    CompetingDiagnosisEngine,
    CompetingDiagnosisResult,
    DiagnosisHypothesis,
    IssueBehaviorExtractor,
    IssueBehaviorMap,
)
from patchforge.diagnosis.evidence import (
    EvidenceItem,
    EvidenceKind,
    EvidencePackage,
)
from patchforge.repair.residual import ResidualFailureRecord
from patchforge.diagnosis.causal_trace import BehavioralTrace, FirstDivergenceLocator
from patchforge.diagnosis.contract import APIContract, ContractExtractor
from patchforge.diagnosis.contract_gate import (
    BehavioralRequirementMatrix,
    HypothesisContractGate,
)
from patchforge.diagnosis.execution_path import ExecutionPathBuilder, ExecutionPathModel
from patchforge.diagnosis.unified_evidence import UnifiedEvidenceEngine
from patchforge.experiment.runner import ExperimentRunner
from patchforge.issue.problem import Problem
from patchforge.memory.episode import RepairEpisode, save_repair_episode
from patchforge.memory.store import CrossTaskRepairMemory, MemoryMode
from patchforge.models.provider import ModelProvider, OllamaProvider
from patchforge.repair.patch import Patch
from patchforge.repair.planner import RepairUnitPlanner
from patchforge.repair.ranking import RepairSiteRanker
from patchforge.repair.reconstructor import SourceReconstructor
from patchforge.repair.replanner import FailureDrivenReplanner, ReplanAction
from patchforge.repair.schema import (
    RankedRepairSite,
    ReconstructedPatch,
    RepairUnit,
    RepairUnitType,
    StaticValidationResult,
    StructuredRepairOutput,
)
from patchforge.repair.structured_repair import (
    SYSTEM_STRUCTURED_REPAIR_PROMPT,
    StructuredRepairParser,
    StructuredRepairPromptBuilder,
)
from patchforge.repair.validator import StaticRepairValidator
from patchforge.repository.map import RepoMap
from patchforge.repository_intelligence.indexer import RepositoryIndexer
from patchforge.repository_intelligence.retriever import RepairContextRetriever
from patchforge.repository_intelligence.temporal_graph import TemporalRepositoryGraph
from patchforge.retrieval.hybrid import HybridRepositoryRetriever
from patchforge.telemetry.events import TelemetryLogger
from patchforge.verification.adaptive import AdaptiveVerificationVerdict, AdaptiveVerifier
from patchforge.verification.classifier import FailureClass
from patchforge.verification.tester import Tester

logger = logging.getLogger(__name__)

SYSTEM_DIAGNOSIS_PROMPT = """You are the Principal Software Diagnosis Engine for PatchForge AI.
Analyze the repository intelligence, structural graph, temporal history, call hierarchy, control flow, and state transitions to provide a rigorous root-cause diagnosis.
Output strictly following the requested diagnostic schema:
=== CAUSE ===
=== EXPECTED BEHAVIOR ===
=== INVARIANT ===
=== EVIDENCE ===
=== DEFECT CONFIDENCE ===
=== REPAIR STRATEGY ===
=== REPAIR JUSTIFICATION ===
=== AFFECTED SITES ===
"""


@dataclass
class V1OrchestratorResult:
    instance_id: str
    target: Optional[RepairTarget] = None
    diagnosis: Optional[CompetingDiagnosisResult] = None
    patch: Optional[Patch] = None
    engine_name: str = "v1"
    generation_attempted: bool = True
    patch_generated: bool = False
    structurally_valid: bool = False
    statically_valid: bool = False
    submitted_to_harness: bool = False
    patch_applied: bool = False
    test_executed: bool = False
    resolved: bool = False
    failure_class: str = FailureClass.UNRESOLVED.value
    runtime_s: float = 0.0
    test_time_s: float = 0.0
    generation_time_s: float = 0.0
    reconstruction_time_s: float = 0.0
    indexing_time_s: Optional[float] = None
    retrieval_diagnosis_time_s: Optional[float] = None
    probing_time_s: Optional[float] = None
    static_validation_time_s: float = 0.0
    replanning_time_s: float = 0.0
    f2p_passed: int = 0
    f2p_total: int = 0
    p2p_passed: int = 0
    p2p_total: int = 0
    telemetry: Dict[str, Any] = field(default_factory=dict)
    test_summary: Dict[str, Any] = field(default_factory=dict)
    refinement_cycles: int = 0
    refinement_history: List[Dict[str, Any]] = field(default_factory=list)
    repair_unit: Optional[RepairUnit] = None
    ranked_sites: List[RankedRepairSite] = field(default_factory=list)
    target_provenance: Dict[str, Any] = field(default_factory=dict)
    validation_result: Optional[StaticValidationResult] = None
    experiment_executed: bool = False
    experiment_summary: str = ""
    memory_mode: str = "FROZEN"
    memory_provenance: Dict[str, Any] = field(default_factory=dict)
    memory_exemplars_used: int = 0
    contract: Optional[APIContract] = None
    requirement_matrix: Optional[BehavioralRequirementMatrix] = None
    execution_path: Optional[ExecutionPathModel] = None
    causal_trace: Optional[BehavioralTrace] = None
    evidence_package: Optional[EvidencePackage] = None
    residual_records: List[ResidualFailureRecord] = field(default_factory=list)
    target_unjustified: bool = False
    repeated_attempt_detected: bool = False


class V1RepairOrchestrator:
    """The complete PatchForge V1 Autonomous Software-Repair Architecture."""

    def __init__(
        self,
        provider: Optional[ModelProvider] = None,
        model_name: str = "qwen2.5-coder:14b",
        workspace: str = ".",
        episodes_dir: str = "results/episodes",
        enable_dynamic_probing: bool = False,
        memory_mode: str = "FROZEN",
    ):
        self.provider = provider or OllamaProvider(model=model_name)
        self.model_name = model_name
        self.workspace = workspace
        self.enable_dynamic_probing = enable_dynamic_probing
        self.memory_mode = memory_mode
        mode_enum = MemoryMode(memory_mode) if isinstance(memory_mode, str) else memory_mode
        self.memory = CrossTaskRepairMemory(episodes_dir=episodes_dir, mode=mode_enum)
    def _rebuild_repair_context(
        self,
        problem: Problem,
        repo_dir: str,
        repo_map: RepoMap,
        graph: Any,
        test_index: Any,
        diagnosis: CompetingDiagnosisResult,
        active_hyp_idx: int,
        context_retriever: RepairContextRetriever,
        api_contract: Optional[APIContract],
        req_matrix: Optional[BehavioralRequirementMatrix],
        candidate_profiles_dict: Dict[str, Any],
        candidate_file: str,
        target_provenance: Dict[str, Any],
    ) -> Tuple[Any, RepairTarget, str, Path, str, Any, RepairUnit]:
        """Rebuilds target, source, repair unit, and AST context upon hypothesis switch."""
        diagnosis.set_active_hypothesis(active_hyp_idx)
        current_h = diagnosis.selected_hypothesis or diagnosis.hypotheses[active_hyp_idx]
        repo_p = Path(repo_dir)

        # Extract candidates for the newly active hypothesis
        h_candidates: List[EditSite] = []
        for aff in current_h.affected_sites:
            if ":" in aff:
                aff_file, aff_sym = aff.split(":", 1)
                aff_file = aff_file.strip("` \t\r\n")
                aff_sym = aff_sym.strip("` \t\r\n()[]")
                aff_full = repo_p / aff_file
                if aff_full.exists():
                    aff_source, a_start, a_end, _ = repo_map.extract_surgical_unit(aff_file, aff_sym, "")
                    if aff_source:
                        h_candidates.append(
                            EditSite(
                                file_path=aff_file,
                                symbol=aff_sym,
                                line_start=a_start,
                                line_end=a_end,
                                source_span=(a_start, a_end),
                                verified_source=aff_source,
                                verification_status=True,
                                site_role="HYPOTHESIS_TARGET",
                                rank=1,
                            )
                        )

        ranker = RepairSiteRanker(graph)
        ranked = ranker.rank_sites(
            candidates=h_candidates,
            problem=problem,
            diagnosis=diagnosis,
            test_traceback=None,
            primary_file=candidate_file,
            candidate_profiles=candidate_profiles_dict,
        ) if h_candidates else []

        selected_site = ranked[0] if ranked else (h_candidates[0] if h_candidates else None)
        if not selected_site:
            top_sym = diagnosis.repair_sites[0].split(":")[-1] if diagnosis.repair_sites else "unknown"
            top_file = diagnosis.repair_sites[0].split(":")[0] if diagnosis.repair_sites else candidate_file
            s_src, a_start, a_end, _ = repo_map.extract_surgical_unit(top_file, top_sym, "")
            selected_site = EditSite(
                file_path=top_file,
                symbol=top_sym,
                line_start=a_start or 1,
                line_end=a_end or 1,
                source_span=(a_start or 1, a_end or 1),
                verified_source=s_src,
                verification_status=bool(s_src),
                site_role="HYPOTHESIS_TARGET",
                rank=1,
            )

        is_coherent, justification = RepairSiteRanker.reconcile_target(selected_site, diagnosis, graph)
        target = RepairTarget(
            repository=problem.repo,
            file_path=selected_site.file_path,
            symbol=selected_site.symbol,
            line_start=selected_site.line_start,
            line_end=selected_site.line_end,
            source_span=(selected_site.line_start, selected_site.line_end),
            verified_source=getattr(selected_site, "verified_source", "") or repo_map.extract_surgical_unit(selected_site.file_path, selected_site.symbol, "")[0],
            verification_status=True,
            behavior_context="",
        )

        target_file_rel = selected_site.file_path
        target_file_abs = repo_p / target_file_rel
        try:
            original_code = target_file_abs.read_text(encoding="utf-8", errors="replace")
        except Exception:
            original_code = target.verified_source

        repair_context = context_retriever.retrieve(
            problem_statement=problem.problem_statement,
            target_file=target.file_path,
            target_symbol=target.symbol,
        )

        unit = RepairUnitPlanner.plan_repair_unit(
            file_path=target_file_rel,
            source_code=original_code,
            target_symbol=selected_site.symbol,
            target_lines=(selected_site.line_start, selected_site.line_end),
            diagnosis=diagnosis,
            role=selected_site.role if hasattr(selected_site, "role") else "PRIMARY",
        )

        if api_contract:
            api_contract.target_symbol = target.symbol

        target_provenance["target_changes"].append({
            "switched_to_hypothesis": current_h.id,
            "target": f"{target.file_path}:{target.symbol}",
            "justification": justification,
        })

        return selected_site, target, original_code, target_file_abs, target_file_rel, repair_context, unit

    def repair(
        self,
        problem: Problem,
        repo_dir: str,
        tester: Optional[Tester] = None,
        max_repair_attempts: int = 3,
        allow_dynamic_probes: bool = True,
    ) -> V1OrchestratorResult:
        """Executes the complete V1 autonomous repair workflow."""
        t0 = time.time()
        telemetry_logger = TelemetryLogger(problem.instance_id)
        result = V1OrchestratorResult(instance_id=problem.instance_id)
        repo_p = Path(repo_dir)

        def _cleanup():
            try:
                subprocess.run(["git", "checkout", "."], cwd=repo_dir, capture_output=True)
                subprocess.run(["git", "clean", "-fd"], cwd=repo_dir, capture_output=True)
            except Exception:
                pass

        _cleanup()

        # Step 1: Repository Intelligence & Temporal Graph Indexing
        t_index0 = time.time()
        indexer = RepositoryIndexer(repo_dir)
        graph, test_index = indexer.index()

        temporal_graph = TemporalRepositoryGraph(repo_dir, code_graph=graph).build(max_commits=50)
        result.indexing_time_s = round(time.time() - t_index0, 3)
        t_diag0 = time.time()
        telemetry_logger.log_event(
            "INDEXING",
            "GRAPHS_LOADED",
            duration_ms=(time.time() - t_index0) * 1000.0,
            details={
                "nodes": len(graph.nodes),
                "edges": graph.g.number_of_edges(),
                "commits": len(temporal_graph.commits),
            },
        )

        # Step 2: Specification & Contract Extraction
        issue_text = (problem.problem_statement or "") + "\n" + (problem.hints_text or "")
        behavior_map = IssueBehaviorExtractor.extract(problem.problem_statement, getattr(problem, "hints_text", ""))
        api_contract = ContractExtractor.extract_contract(problem.problem_statement or "", getattr(problem, "hints_text", "") or "")
        req_matrix = BehavioralRequirementMatrix.decompose(problem.problem_statement or "", getattr(problem, "hints_text", "") or "", contract=api_contract)
        result.contract = api_contract
        result.requirement_matrix = req_matrix

        # Step 3: Hybrid Multi-Channel Retrieval & Localization
        retriever = HybridRepositoryRetriever(
            repo_dir=repo_dir,
            code_graph=graph,
            test_index=test_index,
            temporal_graph=temporal_graph,
        )
        retrieved_cands = retriever.retrieve(
            query=problem.problem_statement or "",
            hints_text=getattr(problem, "hints_text", "") or "",
            traceback_text=None,
            top_k=8,
        )

        repo_map = RepoMap(repo_dir)
        py_files = [
            str(p.relative_to(repo_p)).replace("\\", "/")
            for p in repo_p.glob("**/*.py")
            if p.is_file() and not any(part in ("tests", "testing", "test", "docs", ".git", "venv", ".venv", "build", "dist") for part in p.parts)
        ]
        candidate_file = retrieved_cands[0].file_path if retrieved_cands else (py_files[0] if py_files else "unknown.py")

        # Collect stage 1 candidate edit sites
        stage1_candidates: List[EditSite] = []
        for rc in retrieved_cands[:6]:
            s_src, w_start, w_end, b_ctx = repo_map.extract_surgical_unit(rc.file_path, rc.symbol, issue_text)
            if s_src:
                stage1_candidates.append(
                    EditSite(
                        file_path=rc.file_path,
                        symbol=rc.symbol,
                        line_start=w_start,
                        line_end=w_end,
                        source_span=(w_start, w_end),
                        verified_source=s_src,
                        verification_status=True,
                        behavior_context=b_ctx,
                        site_role="PRIMARY",
                    )
                )

        if not stage1_candidates:
            # Fallback to symbol extraction
            symbols = repo_map.extract_symbols(candidate_file)
            top_sym = symbols[0].name if symbols else "unknown"
            s_src, w_start, w_end, b_ctx = repo_map.extract_surgical_unit(candidate_file, top_sym, issue_text)
            stage1_candidates.append(
                EditSite(
                    file_path=candidate_file,
                    symbol=top_sym,
                    line_start=w_start,
                    line_end=w_end,
                    source_span=(w_start, w_end),
                    verified_source=s_src,
                    verification_status=bool(s_src),
                    behavior_context=b_ctx,
                    site_role="PRIMARY",
                )
            )

        # Step 4: Unified Evidence Engine Aggregation
        evidence_engine = BehavioralEvidenceEngine(repo_dir=repo_dir, graph=graph, test_index=test_index)
        unified_evidence = UnifiedEvidenceEngine(code_graph=graph, temporal_graph=temporal_graph)

        candidate_profiles: List[CandidateProfile] = []
        state_flows: Dict[str, Any] = {}

        for c in stage1_candidates:
            prof = evidence_engine.candidate_analyzer.analyze_candidate(
                symbol=c.symbol,
                file_path=c.file_path,
                start_line=c.line_start,
                end_line=c.line_end,
                behavior_map=behavior_map,
                failing_traceback=None,
            )
            candidate_profiles.append(prof)
            s_flow = evidence_engine.analyze_candidate_state_flow(c.file_path, c.symbol)
            if s_flow:
                state_flows[c.symbol] = s_flow

            t_prof = temporal_graph.get_symbol_profile(c.file_path, c.symbol)
            unified_evidence.aggregate_candidate_evidence(
                profile=prof,
                behavior_map=behavior_map,
                temporal_profile=t_prof,
                state_flow=s_flow,
            )

        # Bounded large-class context if applicable
        class_ctx_str = ""
        symbols_in_file = repo_map.extract_symbols(candidate_file)
        for s in symbols_in_file:
            if s.kind == "class":
                bounded = evidence_engine.extract_bounded_class_context(
                    file_path=candidate_file,
                    class_name=s.name,
                    active_symbols=[c.symbol for c in stage1_candidates],
                )
                if bounded:
                    class_ctx_str = bounded.format_for_prompt()
                    break

        # Cross-Task Memory Context (Respecting memory mode & isolating current instance)
        memory_context, memory_prov = self.memory.retrieve_with_provenance(
            repo=problem.repo,
            query_text=problem.problem_statement or "",
            target_symbol=stage1_candidates[0].symbol if stage1_candidates else "",
            current_instance_id=problem.instance_id,
            top_k=2,
        )
        result.memory_mode = self.memory.mode.value
        result.memory_provenance = memory_prov
        if memory_context:
            result.memory_exemplars_used = len(memory_prov.get("prompt_included_episode_ids", []))

        # Execution Path Modeling & Algorithmic Divergence Detection
        primary_site = stage1_candidates[0] if stage1_candidates else None
        exec_path: Optional[ExecutionPathModel] = None
        if primary_site:
            primary_file_abs = repo_p / primary_site.file_path
            try:
                primary_src = primary_file_abs.read_text(encoding="utf-8", errors="replace")
            except Exception:
                primary_src = primary_site.verified_source or ""

            tb_lines: List[int] = []
            f_name = Path(primary_site.file_path).name
            for m in re.finditer(rf"{re.escape(f_name)}.*?line\s+(\d+)", issue_text, re.IGNORECASE):
                try:
                    tb_lines.append(int(m.group(1)))
                except ValueError:
                    pass

            exec_path = ExecutionPathBuilder.build_path(
                file_path=primary_site.file_path,
                source_code=primary_src,
                target_symbol=primary_site.symbol,
                traceback_lines=tb_lines,
                traceback_text=getattr(problem, "hints_text", "") or "",
                problem_statement=problem.problem_statement or "",
            )
            result.execution_path = exec_path

            # V1.3 Behavioral Trace & First Divergence Pinpointing
            causal_trace = FirstDivergenceLocator.locate_divergence(
                source_code=primary_src,
                target_symbol=primary_site.symbol,
                file_path=primary_site.file_path,
                traceback_lines=tb_lines,
                problem_statement=problem.problem_statement or "",
            )
            result.causal_trace = causal_trace

            # Re-decompose requirement matrix to synthesize algorithmic transition requirements
            req_matrix = BehavioralRequirementMatrix.decompose(
                problem_statement=problem.problem_statement or "",
                hints_text=getattr(problem, "hints_text", "") or "",
                contract=api_contract,
                execution_path=exec_path,
            )
            result.requirement_matrix = req_matrix

        # Step 5: Competing Behavioral Diagnosis & Evidence Packaging
        ev_package = EvidencePackage(
            instance_id=problem.instance_id,
            problem_statement=problem.problem_statement or "",
            expected_behavior=behavior_map.expected_behavior if behavior_map else "",
        )
        ev_package.candidate_symbols = [f"{c.file_path}:{c.symbol}" for c in stage1_candidates]
        for c in stage1_candidates:
            ev_package.add_item(
                kind=EvidenceKind.STATIC_CODE_EVIDENCE,
                category="AST_SURGICAL_UNIT",
                content=f"Symbol `{c.symbol}` in `{c.file_path}` lines {c.line_start}-{c.line_end}",
                provenance=f"{c.file_path}:{c.line_start}",
                target_symbol=c.symbol,
            )
        for p in candidate_profiles:
            if getattr(p, "causal_paths", None):
                ev_package.add_item(
                    kind=EvidenceKind.STATIC_CODE_EVIDENCE,
                    category="CAUSAL_PATH",
                    content=f"{p.symbol} causal path: {p.causal_paths[0].path_str}",
                    provenance=p.file_path,
                    target_symbol=p.symbol,
                )
            if getattr(p, "direct_tests", None):
                ev_package.associated_tests.extend([str(t) for t in p.direct_tests[:3]])
        if api_contract:
            ev_package.contract_invariants = [
                f"{api_contract.target_symbol}: {inv}"
                for inv in (api_contract.behavioral_invariants or [])
            ]
        if test_index and hasattr(test_index, "get_tests_for_symbol"):
            for c in stage1_candidates:
                tests = test_index.get_tests_for_symbol(c.symbol) or []
                if tests:
                    ev_package.associated_tests.extend([str(t) for t in tests[:3]])

        # Observed facts vs unknown information
        hint_text = getattr(problem, "hints_text", "") or ""
        hint_lines_list: List[str] = []
        if hint_text:
            for line in hint_text.splitlines():
                if line.strip():
                    hint_lines_list.append(line.strip())
        if hint_lines_list:
            for h_line in hint_lines_list[:4]:
                ev_package.add_item(
                    kind=EvidenceKind.STATIC_CODE_EVIDENCE,
                    category="PROBLEM_HINT",
                    content=h_line,
                    provenance="hints_text",
                )

        ev_package.add_item(
            kind=EvidenceKind.UNKNOWN_OR_UNAVAILABLE,
            category="DYNAMIC_RUNTIME_TRACE",
            content="No runtime traceback observed prior to repair; avoid fabricating execution trace.",
        )

        if not (self.enable_dynamic_probing and allow_dynamic_probes):
            ev_package.add_item(
                kind=EvidenceKind.UNKNOWN_OR_UNAVAILABLE,
                category="DYNAMIC_EXECUTION_PROBE",
                content="Dynamic probing disabled by configuration; no probe state recorded.",
            )

        result.evidence_package = ev_package

        diag_prompt = CompetingDiagnosisEngine.build_diagnostic_prompt(
            problem_statement=problem.problem_statement,
            behavior_map=behavior_map,
            candidate_profiles=candidate_profiles,
            failure_evidence=ev_package,
            state_flows=state_flows,
            class_context=class_ctx_str or None,
            execution_path_summary=exec_path.format_for_prompt() if exec_path else "",
        )
        diag_prompt = diag_prompt + "\n" + ev_package.format_for_prompt() + "\n"
        # Inject Unified Evidence Substrate
        unified_ev_summary = unified_evidence.format_summary_for_prompt()
        if unified_ev_summary:
            diag_prompt = diag_prompt + "\n" + unified_ev_summary + "\n"

        if causal_trace and causal_trace.events:
            diag_prompt = diag_prompt + "\n" + causal_trace.format_for_prompt() + "\n"

        if api_contract:
            diag_prompt = diag_prompt + "\n" + api_contract.format_for_prompt() + "\n"

        if req_matrix and req_matrix.requirements:
            diag_prompt = diag_prompt + "\n" + req_matrix.format_for_prompt() + "\n"

        if memory_context:
            diag_prompt = memory_context + "\n" + diag_prompt

        try:
            diag_resp = self.provider.generate_one(diag_prompt, system=SYSTEM_DIAGNOSIS_PROMPT)
            diagnosis = CompetingDiagnosisEngine.parse_competing_diagnosis(
                response_text=diag_resp.text,
                candidate_profiles=candidate_profiles,
                behavior_map=behavior_map,
                causal_trace=causal_trace if primary_site else None,
            )
        except Exception as e:
            logger.warning(f"Competing diagnosis fallback: {e}")
            top_sym = stage1_candidates[0].symbol if stage1_candidates else "unknown"
            diagnosis = CompetingDiagnosisResult(
                hypotheses=[
                    DiagnosisHypothesis(
                        id="A",
                        cause="Identified defect in target implementation.",
                        invariant="Preserve caller expectations.",
                        repair_strategy="Fix target behavior.",
                        affected_sites=[f"{candidate_file}:{top_sym}"],
                        confidence=0.8,
                    )
                ],
                selected_hypothesis_idx=0,
                repair_sites=[f"{candidate_file}:{top_sym}"],
            )

        # V1.1 Hypothesis Contract Gate: Filter & eliminate conflicting hypotheses
        diagnosis = HypothesisContractGate.evaluate_and_filter(diagnosis, api_contract, req_matrix)
        result.diagnosis = diagnosis
        result.retrieval_diagnosis_time_s = round(time.time() - t_diag0, 3)

        # Step 6: Candidate Ranking & Selection with Explicit Reconciliation
        all_candidates = list(stage1_candidates)
        for aff in diagnosis.affected_sites:
            if ":" in aff:
                aff_file, aff_sym = aff.split(":", 1)
                aff_file = aff_file.strip("` \t\r\n")
                aff_sym = aff_sym.strip("` \t\r\n()[]")
                aff_full = repo_p / aff_file
                if aff_full.exists() and not any(s.file_path == aff_file and s.symbol == aff_sym for s in all_candidates):
                    aff_source, a_start, a_end, _ = repo_map.extract_surgical_unit(aff_file, aff_sym, "")
                    if aff_source:
                        all_candidates.append(
                            EditSite(
                                file_path=aff_file,
                                symbol=aff_sym,
                                line_start=a_start,
                                line_end=a_end,
                                source_span=(a_start, a_end),
                                verified_source=aff_source,
                                verification_status=True,
                                site_role="DIAGNOSIS_DERIVED",
                                rank=1,
                            )
                        )

        cand_profiles_dict: Dict[str, Any] = {}
        for p in candidate_profiles:
            if hasattr(p, "file_path") and p.file_path:
                norm_file = p.file_path.replace("\\", "/")
                cand_profiles_dict[f"{norm_file}:{p.symbol}"] = p
                cand_profiles_dict[f"{norm_file.split('/')[-1]}:{p.symbol}"] = p
            if p.symbol not in cand_profiles_dict:
                cand_profiles_dict[p.symbol] = p
            short_sym = p.symbol.split(".")[-1]
            if short_sym not in cand_profiles_dict:
                cand_profiles_dict[short_sym] = p

        ranker = RepairSiteRanker(graph)
        ranked_sites = ranker.rank_sites(
            candidates=all_candidates,
            problem=problem,
            diagnosis=diagnosis,
            test_traceback=ev_package.runtime_tracebacks if ev_package.runtime_tracebacks else None,
            primary_file=candidate_file,
            behavior_map=behavior_map,
            candidate_profiles=cand_profiles_dict,
        )
        result.ranked_sites = ranked_sites

        if not ranked_sites and not stage1_candidates:
            result.resolved = False
            result.failure_class = FailureClass.UNRESOLVED.value
            result.runtime_s = round(time.time() - t0, 2)
            return result

        # Reconcile target with diagnosis
        selected_site = ranked_sites[0] if ranked_sites else stage1_candidates[0]
        is_coherent, justification = RepairSiteRanker.reconcile_target(selected_site, diagnosis, graph)
        if not is_coherent:
            for cand in ranked_sites[1:]:
                c_coh, c_just = RepairSiteRanker.reconcile_target(cand, diagnosis, graph)
                if c_coh:
                    selected_site = cand
                    is_coherent = True
                    justification = c_just
                    break

        # Priority B: Prevent target drift. If ranker still diverged, bind directly to hypothesized site
        if not is_coherent and diagnosis.affected_sites:
            for aff in diagnosis.affected_sites:
                if ":" in aff:
                    aff_file, aff_sym = aff.split(":", 1)
                    aff_file = aff_file.strip("` \t\r\n")
                    aff_sym = aff_sym.strip("` \t\r\n()[]")
                    aff_full = repo_p / aff_file
                    if aff_full.exists():
                        aff_source, a_start, a_end, _ = repo_map.extract_surgical_unit(aff_file, aff_sym, "")
                        if aff_source:
                            selected_site = EditSite(
                                file_path=aff_file,
                                symbol=aff_sym,
                                line_start=a_start or 1,
                                line_end=a_end or 1,
                                source_span=(a_start or 1, a_end or 1),
                                verified_source=aff_source,
                                verification_status=True,
                                site_role="DIAGNOSIS_FORCED",
                                rank=1,
                            )
                            is_coherent, justification = RepairSiteRanker.reconcile_target(selected_site, diagnosis, graph)
                            break

        if not is_coherent or not getattr(selected_site, "verification_status", False):
            result.target_unjustified = True

        target_provenance = {
            "original_diagnosis_sites": getattr(diagnosis, "affected_sites", []),
            "selected_target": f"{selected_site.file_path}:{selected_site.symbol}",
            "matches_hypothesized_site": is_coherent and "Target matches" in justification,
            "justification": justification,
            "target_unjustified": result.target_unjustified,
            "target_changes": [],
        }
        result.target_provenance = target_provenance

        target = RepairTarget(
            repository=problem.repo,
            file_path=selected_site.file_path,
            symbol=selected_site.symbol,
            line_start=selected_site.line_start,
            line_end=selected_site.line_end,
            source_span=(selected_site.line_start, selected_site.line_end),
            verified_source=getattr(selected_site, "verified_source", "") or repo_map.extract_surgical_unit(selected_site.file_path, selected_site.symbol, "")[0],
            verification_status=True,
            behavior_context="",
        )
        result.target = target
        if api_contract:
            api_contract.target_symbol = target.symbol
        target_file_rel = selected_site.file_path
        target_file_abs = repo_p / target_file_rel

        # Step 7: Dynamic Sandboxed Probing & Executable Counterfactuals (Gated via configuration)
        if self.enable_dynamic_probing and allow_dynamic_probes and tester and len(diagnosis.hypotheses) >= 2:
            t_probe0 = time.time()
            probe_runner = ExperimentRunner(repo_dir=repo_dir, tester=tester)
            target_probe_line = target.line_start
            if primary_site and causal_trace and causal_trace.first_divergence_line:
                target_probe_line = causal_trace.first_divergence_line
            elif exec_path and exec_path.divergence_node:
                target_probe_line = exec_path.divergence_node.line_number
            exp_res = probe_runner.run_experiment(
                instance_id=problem.instance_id,
                diagnosis=diagnosis,
                target_file=target.file_path,
                target_symbol=target.symbol,
                target_line=target_probe_line,
            )
            result.probing_time_s = round(time.time() - t_probe0, 3)
            result.experiment_executed = True
            result.experiment_summary = exp_res.summary
            telemetry_logger.log_event("EXPERIMENT", "PROBE_EXECUTED", details=exp_res.to_dict())
        else:
            result.probing_time_s = None
            result.experiment_executed = False
            result.experiment_summary = "Dynamic probing disabled by default (V1.6 reliability configuration)."

        try:
            original_code = target_file_abs.read_text(encoding="utf-8", errors="replace")
        except Exception:
            original_code = target.verified_source

        # Retrieve grounded graph context (callers, callees, state-flow)
        context_retriever = RepairContextRetriever(repo_dir, graph, test_index)
        repair_context = context_retriever.retrieve(
            problem_statement=problem.problem_statement,
            target_file=target.file_path,
            target_symbol=target.symbol,
        )

        # Step 8: AST Repair Planning
        unit = RepairUnitPlanner.plan_repair_unit(
            file_path=target_file_rel,
            source_code=original_code,
            target_symbol=selected_site.symbol,
            target_lines=(selected_site.line_start, selected_site.line_end),
            diagnosis=diagnosis,
            role=selected_site.role if hasattr(selected_site, "role") else "PRIMARY",
        )
        result.repair_unit = unit

        # Step 9: Structured Repair Synthesis, Deterministic Rebuild & Adaptive Verification Loop
        verifier = AdaptiveVerifier(tester=tester)
        last_error = ""
        current_action: Optional[ReplanAction] = None
        patch_applied = False
        applied_patch: Optional[Patch] = None
        attempt = 0
        active_hyp_idx = getattr(diagnosis, "selected_hypothesis_idx", 0)
        if not (0 <= active_hyp_idx < len(diagnosis.hypotheses)):
            active_hyp_idx = 0
        diagnosis.set_active_hypothesis(active_hyp_idx)
        patch_hashes: set[str] = set()
        attempt_signatures: set[str] = set()
        last_working_replacement: Optional[str] = None
        last_residual_record: Optional[ResidualFailureRecord] = None
        last_failure_class: Optional[str] = None
        last_f2p_passed: int = 0

        while attempt < max_repair_attempts:
            attempt += 1
            result.refinement_cycles = attempt
            llm_t0 = time.time()

            # Choose prompt strategy based on attempt and replanning
            if attempt == 1:
                prompt_to_use = StructuredRepairPromptBuilder.build_repair_prompt(
                    unit=unit,
                    problem=problem,
                    diagnosis=diagnosis,
                    callers=[c.name for c in repair_context.callers[:4]] if repair_context else [],
                    callees=[c.name for c in repair_context.callees[:4]] if repair_context else [],
                    state_flow_summary=repair_context.state_flow or "" if repair_context else "",
                    full_source=original_code,
                    contract_spec=api_contract.format_for_prompt() if api_contract else "",
                    requirement_matrix_spec=req_matrix.format_for_prompt() if req_matrix else "",
                )
                strategy_name = "strict_structured_json"
            elif (attempt == 2 or (attempt == 3 and current_action == ReplanAction.REFINE_SEMANTICS)) and current_action != ReplanAction.FALLBACK_SYNTAX:
                res_guidance = (
                    last_residual_record.format_corrective_guidance()
                    if (last_residual_record and last_residual_record.is_partial_progress)
                    else None
                )
                prompt_to_use = StructuredRepairPromptBuilder.build_repair_prompt(
                    unit=unit,
                    problem=problem,
                    diagnosis=diagnosis,
                    callers=[c.name for c in repair_context.callers[:4]] if repair_context else [],
                    callees=[c.name for c in repair_context.callees[:4]] if repair_context else [],
                    state_flow_summary=repair_context.state_flow or "" if repair_context else "",
                    full_source=original_code,
                    contract_spec=api_contract.format_for_prompt() if api_contract else "",
                    requirement_matrix_spec=req_matrix.format_for_prompt() if req_matrix else "",
                    error_feedback=last_error,
                    residual_guidance=res_guidance,
                    prior_patch_code=last_working_replacement if last_working_replacement else None,
                )
                strategy_name = "refined_structured_json"
            elif attempt == 2 or (attempt == 3 and current_action == ReplanAction.FALLBACK_SYNTAX):
                prompt_to_use = StructuredRepairPromptBuilder.build_minimal_prompt(
                    unit=unit,
                    error_feedback=last_error,
                    problem_statement=problem.problem_statement[:1000] if problem else "",
                    diagnosis_summary=f"{diagnosis.cause} -> {diagnosis.repair_strategy}" if diagnosis else "",
                    prior_patch_code=last_working_replacement or "",
                )
                strategy_name = "minimal_target_bound_json"
            else:
                prompt_to_use = StructuredRepairPromptBuilder.build_replacement_only_prompt(
                    unit=unit,
                    error_feedback=last_error,
                    problem_statement=problem.problem_statement[:1000] if problem else "",
                    diagnosis_summary=f"{diagnosis.cause} -> {diagnosis.repair_strategy}" if diagnosis else "",
                    prior_patch_code=last_working_replacement or "",
                )
                strategy_name = "replacement_only_code"

            result.generation_attempted = True
            llm_t0 = time.time()
            try:
                resp = self.provider.generate_one(prompt_to_use, system=SYSTEM_STRUCTURED_REPAIR_PROMPT)
                result.generation_time_s += round(time.time() - llm_t0, 3)
                structured_out = StructuredRepairParser.parse(resp.text, unit)
                result.patch_generated = True
            except Exception as e:
                err_msg = str(e)
                last_error = err_msg
                if "no-op repair" in err_msg or "identical" in err_msg.lower():
                    fail_cls = FailureClass.UNRESOLVED.value
                elif "AMBIGUOUS" in err_msg or "JSON" in err_msg or "schema" in err_msg.lower():
                    fail_cls = FailureClass.REPAIR_SCHEMA_FAILURE.value
                else:
                    fail_cls = FailureClass.PATCH_SYNTAX.value

                attempt_sig = f"{unit.file_path}:{unit.symbol}:{strategy_name}:{active_hyp_idx}:{err_msg}"
                is_repeated = attempt_sig in attempt_signatures
                attempt_signatures.add(attempt_sig)
                if is_repeated:
                    result.repeated_attempt_detected = True

                result.failure_class = fail_cls
                current_hyp_id = diagnosis.hypotheses[active_hyp_idx].id if active_hyp_idx < len(diagnosis.hypotheses) else ""
                result.refinement_history.append({
                    "attempt": attempt,
                    "hypothesis_id": current_hyp_id,
                    "strategy": strategy_name,
                    "static_valid": False,
                    "tests_executed": False,
                    "resolved": False,
                    "failure_class": fail_cls,
                    "error_message": err_msg,
                    "patch_text": "",
                })

                t_replan0 = time.time()
                decision = FailureDrivenReplanner.decide(
                    failure_class=fail_cls,
                    attempt=attempt,
                    max_attempts=max_repair_attempts,
                    hypotheses_available=len(diagnosis.hypotheses),
                    active_hypothesis_idx=active_hyp_idx,
                    error_message=err_msg,
                    residual_record=last_residual_record,
                    repeated_attempt=is_repeated,
                    available_hypotheses=diagnosis.hypotheses,
                )
                result.replanning_time_s += round(time.time() - t_replan0, 3)
                telemetry_logger.log_event("REPLAN", "PREVERIFY_PARSE_REPLAN", details=decision.__dict__)
                current_action = decision.action
                if decision.action == ReplanAction.ABORT:
                    _cleanup()
                    break
                if decision.action == ReplanAction.SWITCH_HYPOTHESIS and decision.target_hypothesis_id:
                    target_idx = None
                    for idx, h in enumerate(diagnosis.hypotheses):
                        if h.id == decision.target_hypothesis_id:
                            target_idx = idx
                            break
                    active_hyp_idx = target_idx if target_idx is not None else (active_hyp_idx + 1)
                    if active_hyp_idx < len(diagnosis.hypotheses):
                        (
                            selected_site,
                            target,
                            original_code,
                            target_file_abs,
                            target_file_rel,
                            repair_context,
                            unit,
                        ) = self._rebuild_repair_context(
                            problem=problem,
                            repo_dir=repo_dir,
                            repo_map=repo_map,
                            graph=graph,
                            test_index=test_index,
                            diagnosis=diagnosis,
                            active_hyp_idx=active_hyp_idx,
                            context_retriever=context_retriever,
                            api_contract=api_contract,
                            req_matrix=req_matrix,
                            candidate_profiles_dict=cand_profiles_dict,
                            candidate_file=candidate_file,
                            target_provenance=target_provenance,
                        )
                        result.target = target
                        result.repair_unit = unit
                        last_working_replacement = None

                last_error = decision.prompt_guidance or err_msg
                _cleanup()
                continue

            # Deterministic Source Reconstruction (supporting coordinated multi-site repairs)
            rec_t0 = time.time()
            if getattr(structured_out, "sub_edits", None):
                files_source_multi = {unit.file_path: original_code}
                edits_multi: list[tuple[RepairUnit, StructuredRepairOutput]] = []
                if structured_out.replacement and structured_out.repair_action != RepairAction.MULTI_SITE.value:
                    edits_multi.append((unit, structured_out))
                for se in structured_out.sub_edits:
                    se_file = se.get("file_path", unit.file_path).replace("\\", "/")
                    se_sym = se.get("symbol", unit.symbol)
                    se_action = se.get("repair_action", RepairAction.REPLACE_STATEMENT.value)
                    se_rep = se.get("replacement", "")
                    if se_file not in files_source_multi:
                        f_abs = repo_p / se_file
                        if f_abs.exists():
                            files_source_multi[se_file] = f_abs.read_text(encoding="utf-8", errors="replace")
                    se_source = files_source_multi.get(se_file, "")
                    if se_source:
                        se_unit = RepairUnitPlanner.plan_repair_unit(
                            file_path=se_file,
                            source_code=se_source,
                            target_symbol=se_sym,
                            diagnosis=diagnosis,
                        )
                        se_out = StructuredRepairOutput(
                            repair_action=se_action,
                            target_unit_id=se_unit.id,
                            target_symbol=se_sym,
                            replacement=se_rep,
                            reasoning=se.get("reasoning", ""),
                        )
                        edits_multi.append((se_unit, se_out))
                if edits_multi:
                    reconstructed = SourceReconstructor.reconstruct_multi(
                        files_source=files_source_multi,
                        edits=edits_multi,
                    )
                else:
                    reconstructed = SourceReconstructor.reconstruct_single(
                        original_code=original_code,
                        unit=unit,
                        output=structured_out,
                        file_rel_path=unit.file_path,
                    )
            else:
                reconstructed = SourceReconstructor.reconstruct_single(
                    original_code=original_code,
                    unit=unit,
                    output=structured_out,
                    file_rel_path=unit.file_path,
                )
            result.reconstruction_time_s += round(time.time() - rec_t0, 3)

            if not reconstructed.success:
                rec_err = reconstructed.error or "Reconstruction failed"
                last_error = rec_err
                result.structurally_valid = False
                if "No changes produced" in rec_err or "identical" in rec_err.lower():
                    fail_cls = FailureClass.UNRESOLVED.value
                else:
                    fail_cls = FailureClass.REPAIR_VALIDATION_FAILURE.value

                attempt_sig = f"{unit.file_path}:{unit.symbol}:{strategy_name}:{active_hyp_idx}:{rec_err}"
                is_repeated = attempt_sig in attempt_signatures
                attempt_signatures.add(attempt_sig)
                if is_repeated:
                    result.repeated_attempt_detected = True

                result.failure_class = fail_cls
                current_hyp_id = diagnosis.hypotheses[active_hyp_idx].id if active_hyp_idx < len(diagnosis.hypotheses) else ""
                result.refinement_history.append({
                    "attempt": attempt,
                    "hypothesis_id": current_hyp_id,
                    "strategy": strategy_name,
                    "static_valid": False,
                    "tests_executed": False,
                    "resolved": False,
                    "failure_class": fail_cls,
                    "error_message": rec_err,
                    "patch_text": "",
                })

                t_replan0 = time.time()
                decision = FailureDrivenReplanner.decide(
                    failure_class=fail_cls,
                    attempt=attempt,
                    max_attempts=max_repair_attempts,
                    hypotheses_available=len(diagnosis.hypotheses),
                    active_hypothesis_idx=active_hyp_idx,
                    error_message=rec_err,
                    residual_record=last_residual_record,
                    repeated_attempt=is_repeated,
                    available_hypotheses=diagnosis.hypotheses,
                )
                result.replanning_time_s += round(time.time() - t_replan0, 3)
                telemetry_logger.log_event("REPLAN", "PREVERIFY_RECONSTRUCT_REPLAN", details=decision.__dict__)
                current_action = decision.action
                if decision.action == ReplanAction.ABORT:
                    _cleanup()
                    break
                if decision.action == ReplanAction.SWITCH_HYPOTHESIS and decision.target_hypothesis_id:
                    target_idx = None
                    for idx, h in enumerate(diagnosis.hypotheses):
                        if h.id == decision.target_hypothesis_id:
                            target_idx = idx
                            break
                    active_hyp_idx = target_idx if target_idx is not None else (active_hyp_idx + 1)
                    if active_hyp_idx < len(diagnosis.hypotheses):
                        (
                            selected_site,
                            target,
                            original_code,
                            target_file_abs,
                            target_file_rel,
                            repair_context,
                            unit,
                        ) = self._rebuild_repair_context(
                            problem=problem,
                            repo_dir=repo_dir,
                            repo_map=repo_map,
                            graph=graph,
                            test_index=test_index,
                            diagnosis=diagnosis,
                            active_hyp_idx=active_hyp_idx,
                            context_retriever=context_retriever,
                            api_contract=api_contract,
                            req_matrix=req_matrix,
                            candidate_profiles_dict=cand_profiles_dict,
                            candidate_file=candidate_file,
                            target_provenance=target_provenance,
                        )
                        result.target = target
                        result.repair_unit = unit
                        last_working_replacement = None

                last_error = decision.prompt_guidance or rec_err
                _cleanup()
                continue

            result.structurally_valid = True
            current_hyp_id = diagnosis.hypotheses[active_hyp_idx].id if active_hyp_idx < len(diagnosis.hypotheses) else ""

            # Write to disk temporarily for verification with transactional backup and rollback
            orig_sources_map = {}
            written_backups = []
            write_failed = False
            write_err_msg = ""
            for mod_rel, mod_code in reconstructed.modified_contents.items():
                dest_f = repo_p / mod_rel
                dest_f.parent.mkdir(parents=True, exist_ok=True)
                if mod_rel == unit.file_path:
                    orig_sources_map[mod_rel] = original_code
                elif dest_f.exists():
                    orig_sources_map[mod_rel] = dest_f.read_text(encoding="utf-8", errors="replace")
                else:
                    orig_sources_map[mod_rel] = None

                try:
                    dest_f.write_text(mod_code, encoding="utf-8")
                    written_backups.append((dest_f, orig_sources_map[mod_rel]))
                except Exception as write_err:
                    write_failed = True
                    write_err_msg = str(write_err)
                    logger.error(f"Failed to write reconstructed file {mod_rel}: {write_err}")
                    # Roll back all written files to prior state
                    for wf, prior_content in written_backups:
                        try:
                            if prior_content is None:
                                if wf.exists():
                                    wf.unlink()
                            else:
                                wf.write_text(prior_content, encoding="utf-8")
                        except Exception:
                            pass
                    break

            if write_failed:
                _cleanup()
                result.refinement_history.append({
                    "attempt": attempt,
                    "hypothesis_id": current_hyp_id,
                    "strategy": strategy_name,
                    "static_valid": False,
                    "tests_executed": False,
                    "resolved": False,
                    "failure_class": FailureClass.PATCH_SYNTAX.value,
                    "error_message": f"Transactional multi-file write failed: {write_err_msg}",
                    "patch_text": "",
                })
                continue

            diff_proc = subprocess.run(["git", "diff"], cwd=repo_dir, capture_output=True, text=True)
            candidate_diff = diff_proc.stdout or reconstructed.patch_text
            if req_matrix:
                req_matrix.evaluate(candidate_diff)

            # Pre-verification loop breaker: reject duplicate candidate patches before running expensive containers
            candidate_patch_hash = hashlib.sha256(candidate_diff.encode("utf-8")).hexdigest()[:16]
            if candidate_patch_hash in patch_hashes:
                result.repeated_attempt_detected = True
                telemetry_logger.log_event("REPAIR", "EARLY_LOOP_BREAKER_DUPLICATE_PATCH", details={"hash": candidate_patch_hash, "attempt": attempt})
                result.refinement_history.append({
                    "attempt": attempt,
                    "hypothesis_id": current_hyp_id,
                    "strategy": strategy_name,
                    "static_valid": True,
                    "tests_executed": False,
                    "resolved": False,
                    "failure_class": FailureClass.UNRESOLVED.value,
                    "error_message": f"Pre-verification loop breaker: duplicate patch hash {candidate_patch_hash} already tested.",
                    "patch_text": candidate_diff,
                })
                t_replan0 = time.time()
                decision = FailureDrivenReplanner.decide(
                    failure_class=FailureClass.UNRESOLVED.value,
                    attempt=attempt,
                    max_attempts=max_repair_attempts,
                    hypotheses_available=len(diagnosis.hypotheses),
                    active_hypothesis_idx=active_hyp_idx,
                    f2p_passed=last_f2p_passed,
                    p2p_failed=0,
                    error_message=f"Duplicate patch hash {candidate_patch_hash}",
                    residual_record=last_residual_record,
                    repeated_attempt=True,
                    available_hypotheses=diagnosis.hypotheses,
                )
                result.replanning_time_s += round(time.time() - t_replan0, 3)
                telemetry_logger.log_event("REPLAN", "EARLY_LOOP_BREAKER_REPLAN", details=decision.__dict__)
                current_action = decision.action
                if decision.action == ReplanAction.ABORT:
                    _cleanup()
                    break
                if decision.action == ReplanAction.SWITCH_HYPOTHESIS and decision.target_hypothesis_id:
                    target_idx = None
                    for idx, h in enumerate(diagnosis.hypotheses):
                        if h.id == decision.target_hypothesis_id:
                            target_idx = idx
                            break
                    active_hyp_idx = target_idx if target_idx is not None else (active_hyp_idx + 1)
                    if active_hyp_idx < len(diagnosis.hypotheses):
                        (
                            selected_site,
                            target,
                            original_code,
                            target_file_abs,
                            target_file_rel,
                            repair_context,
                            unit,
                        ) = self._rebuild_repair_context(
                            problem=problem,
                            repo_dir=repo_dir,
                            repo_map=repo_map,
                            graph=graph,
                            test_index=test_index,
                            diagnosis=diagnosis,
                            active_hyp_idx=active_hyp_idx,
                            context_retriever=context_retriever,
                            api_contract=api_contract,
                            req_matrix=req_matrix,
                            candidate_profiles_dict=cand_profiles_dict,
                            candidate_file=candidate_file,
                            target_provenance=target_provenance,
                        )
                        result.target = target
                        result.repair_unit = unit
                        last_working_replacement = None

                last_error = decision.prompt_guidance or f"Identical patch proposed again: {candidate_patch_hash}"
                _cleanup()
                continue

            patch_hashes.add(candidate_patch_hash)

            applied_patch = Patch(
                hypothesis_id=f"STRUCTURED_V1_ATT_{attempt}",
                files_changed=reconstructed.files_changed,
                symbols_changed=reconstructed.symbols_changed,
                patch_text=candidate_diff,
                valid=True,
                match_tier="AST_GROUNDED",
                input_tokens=getattr(resp, "input_tokens", 0),
                output_tokens=getattr(resp, "output_tokens", 0),
                new_contents=reconstructed.modified_contents,
            )

            # Adaptive Tiered Verification
            test_t0 = time.time()
            result.submitted_to_harness = True
            verdict: AdaptiveVerificationVerdict = verifier.verify(
                instance_id=problem.instance_id,
                patch=applied_patch,
                original_sources=orig_sources_map or {unit.file_path: original_code},
                reconstructed=reconstructed,
                target_units=reconstructed.units_applied if reconstructed.units_applied else [unit],
                contract=api_contract,
            )
            result.test_time_s += round(time.time() - test_t0, 3)

            result.test_executed = verdict.tests_executed
            result.resolved = verdict.resolved
            result.failure_class = verdict.failure_class
            result.test_summary = verdict.test_summary
            val_t0 = time.time()
            val_res = StaticRepairValidator.validate(
                original_sources={unit.file_path: original_code},
                reconstructed=reconstructed,
                target_units=[unit],
                contract=api_contract,
            )
            result.static_validation_time_s += round(time.time() - val_t0, 3)
            result.validation_result = val_res

            current_hyp_id = diagnosis.hypotheses[active_hyp_idx].id if active_hyp_idx < len(diagnosis.hypotheses) else ""
            result.refinement_history.append({
                "attempt": attempt,
                "hypothesis_id": current_hyp_id,
                "strategy": strategy_name,
                "static_valid": verdict.static_valid,
                "tests_executed": verdict.tests_executed,
                "resolved": verdict.resolved,
                "failure_class": verdict.failure_class,
                "error_message": verdict.error_message,
                "patch_text": applied_patch.patch_text if applied_patch else "",
            })

            # Record residual failure diagnosis (Priority C)
            res_rec = ResidualFailureRecord.from_verdict(
                instance_id=problem.instance_id,
                attempt=attempt,
                verdict=verdict,
                patch_text=applied_patch.patch_text if applied_patch else "",
                target_str=f"{target.file_path}:{target.symbol}",
                req_matrix=req_matrix,
            )
            result.residual_records.append(res_rec)
            last_residual_record = res_rec

            # If partial progress was achieved, capture the replacement code to preserve working invariants
            if res_rec.is_partial_progress and (getattr(structured_out, "replacement", None) or getattr(structured_out, "replacement_code", None)):
                last_working_replacement = getattr(structured_out, "replacement", None) or getattr(structured_out, "replacement_code", "")

            # Loop breaker & repeated ineffective attempt detection (Priority D)
            p_text = applied_patch.patch_text if applied_patch else ""
            patch_hash = hashlib.sha256(p_text.encode("utf-8")).hexdigest()[:16]
            attempt_sig = f"{unit.file_path}:{unit.symbol}:{patch_hash}:{verdict.failure_class}"
            is_repeated = False
            if patch_hash in patch_hashes or attempt_sig in attempt_signatures or (
                attempt > 1
                and verdict.failure_class == last_failure_class
                and verdict.f2p_passed == last_f2p_passed
                and verdict.f2p_passed == 0
            ):
                is_repeated = True
                result.repeated_attempt_detected = True

            patch_hashes.add(patch_hash)
            attempt_signatures.add(attempt_sig)
            last_failure_class = verdict.failure_class
            last_f2p_passed = verdict.f2p_passed

            if verdict.resolved:
                patch_applied = True
                result.patch = applied_patch
                result.patch_applied = True
                break

            # Failure-Driven Replanning
            t_replan0 = time.time()
            decision = FailureDrivenReplanner.decide(
                failure_class=verdict.failure_class,
                attempt=attempt,
                max_attempts=max_repair_attempts,
                hypotheses_available=len(diagnosis.hypotheses),
                active_hypothesis_idx=active_hyp_idx,
                f2p_passed=verdict.f2p_passed,
                p2p_failed=verdict.p2p_total - verdict.p2p_passed if verdict.p2p_total else 0,
                error_message=verdict.error_message,
                residual_record=res_rec,
                repeated_attempt=is_repeated,
                available_hypotheses=diagnosis.hypotheses,
            )
            result.replanning_time_s += round(time.time() - t_replan0, 3)

            telemetry_logger.log_event("REPLAN", "FAILURE_REPLAN_DECISION", details=decision.__dict__)
            current_action = decision.action

            if decision.action == ReplanAction.ABORT:
                _cleanup()
                break

            if decision.action == ReplanAction.SWITCH_HYPOTHESIS and decision.target_hypothesis_id:
                target_idx = None
                for idx, h in enumerate(diagnosis.hypotheses):
                    if h.id == decision.target_hypothesis_id:
                        target_idx = idx
                        break
                active_hyp_idx = target_idx if target_idx is not None else (active_hyp_idx + 1)
                if active_hyp_idx < len(diagnosis.hypotheses):
                    (
                        selected_site,
                        target,
                        original_code,
                        target_file_abs,
                        target_file_rel,
                        repair_context,
                        unit,
                    ) = self._rebuild_repair_context(
                        problem=problem,
                        repo_dir=repo_dir,
                        repo_map=repo_map,
                        graph=graph,
                        test_index=test_index,
                        diagnosis=diagnosis,
                        active_hyp_idx=active_hyp_idx,
                        context_retriever=context_retriever,
                        api_contract=api_contract,
                        req_matrix=req_matrix,
                        candidate_profiles_dict=cand_profiles_dict,
                        candidate_file=candidate_file,
                        target_provenance=target_provenance,
                    )
                    result.target = target
                    result.repair_unit = unit
                    last_working_replacement = None

            last_error = decision.prompt_guidance or verdict.error_message
            _cleanup()

        result.patch = applied_patch
        result.patch_applied = patch_applied
        result.runtime_s = round(time.time() - t0, 2)
        telemetry_breakdown = telemetry_logger.get_breakdown()
        telemetry_breakdown.update({
            "indexing_time_s": result.indexing_time_s,
            "retrieval_diagnosis_time_s": result.retrieval_diagnosis_time_s,
            "probing_time_s": result.probing_time_s,
            "generation_time_s": round(result.generation_time_s, 3),
            "reconstruction_time_s": round(result.reconstruction_time_s, 3),
            "static_validation_time_s": round(result.static_validation_time_s, 3),
            "test_time_s": round(result.test_time_s, 3),
            "replanning_time_s": round(result.replanning_time_s, 3),
            "runtime_s": result.runtime_s,
        })
        result.telemetry = telemetry_breakdown

        # Step 10: Persistent Episode Storage
        try:
            f2p_p = result.test_summary.get("fail_to_pass", {}).get("passed", 0)
            f2p_t = result.test_summary.get("fail_to_pass", {}).get("total", 0)
            p2p_p = result.test_summary.get("pass_to_pass", {}).get("passed", 0)
            p2p_t = result.test_summary.get("pass_to_pass", {}).get("total", 0)

            episode = RepairEpisode(
                instance_id=problem.instance_id,
                repo=problem.repo,
                base_commit=problem.base_commit,
                model_name=self.model_name,
                target=target.to_dict(),
                diagnosis=diagnosis.to_dict() if hasattr(diagnosis, "to_dict") else diagnosis.__dict__,
                causal_chain=[diagnosis.repair_strategy] if diagnosis else [],
                resolved=result.resolved,
                patch_applied=result.patch_applied,
                patch_valid=bool(result.patch and result.patch.valid),
                failure_class=result.failure_class,
                runtime_s=result.runtime_s,
                f2p_passed=f2p_p,
                f2p_total=f2p_t,
                p2p_passed=p2p_p,
                p2p_total=p2p_t,
                patch_text=applied_patch.patch_text if applied_patch else "",
                test_summary=result.test_summary,
                created_at=time.strftime("%Y-%m-%d %H:%M:%S"),
                repair_unit=unit.to_dict() if unit else {},
                ranked_candidates=[s.to_dict() for s in ranked_sites[:5]],
                validation_result=result.validation_result.to_dict() if result.validation_result else {},
            )
            self.memory.record_episode(episode)
        except Exception as e:
            logger.debug(f"Failed to record repair episode: {e}")

        _cleanup()
        return result

    def run(
        self,
        problem: Problem,
        repo_dir: str,
        tester: Optional[Tester] = None,
        max_retries: int = 3,
        max_refinements: int = 3,
        **kwargs,
    ) -> V1OrchestratorResult:
        """Cohort runner entry point."""
        attempts = max(max_retries, max_refinements)
        return self.repair(
            problem=problem,
            repo_dir=repo_dir,
            tester=tester,
            max_repair_attempts=attempts,
        )
