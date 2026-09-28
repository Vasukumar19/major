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
from patchforge.memory.store import CrossTaskRepairMemory
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
    patch_applied: bool = False
    test_executed: bool = False
    resolved: bool = False
    failure_class: str = FailureClass.UNRESOLVED.value
    runtime_s: float = 0.0
    telemetry: Dict[str, Any] = field(default_factory=dict)
    test_summary: Dict[str, Any] = field(default_factory=dict)
    refinement_cycles: int = 0
    refinement_history: List[Dict[str, Any]] = field(default_factory=list)
    repair_unit: Optional[RepairUnit] = None
    ranked_sites: List[RankedRepairSite] = field(default_factory=list)
    validation_result: Optional[StaticValidationResult] = None
    experiment_executed: bool = False
    experiment_summary: str = ""
    memory_exemplars_used: int = 0
    contract: Optional[APIContract] = None
    requirement_matrix: Optional[BehavioralRequirementMatrix] = None
    execution_path: Optional[ExecutionPathModel] = None


class V1RepairOrchestrator:
    """The complete PatchForge V1 Autonomous Software-Repair Architecture."""

    def __init__(
        self,
        provider: Optional[ModelProvider] = None,
        model_name: str = "qwen2.5-coder:14b",
        workspace: str = ".",
        episodes_dir: str = "results/episodes",
    ):
        self.provider = provider or OllamaProvider(model=model_name)
        self.model_name = model_name
        self.workspace = workspace
        self.memory = CrossTaskRepairMemory(episodes_dir=episodes_dir)

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
            query=issue_text,
            traceback_text=getattr(problem, "hints_text", "") or "",
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

        # Cross-Task Memory Context
        memory_context = self.memory.format_memory_context(
            repo=problem.repo,
            query_text=problem.problem_statement,
            target_symbol=stage1_candidates[0].symbol if stage1_candidates else "",
        )
        if memory_context:
            result.memory_exemplars_used = 1

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

            # Re-decompose requirement matrix to synthesize algorithmic transition requirements
            req_matrix = BehavioralRequirementMatrix.decompose(
                problem_statement=problem.problem_statement or "",
                hints_text=getattr(problem, "hints_text", "") or "",
                contract=api_contract,
                execution_path=exec_path,
            )
            result.requirement_matrix = req_matrix

        # Step 5: Competing Behavioral Diagnosis
        diag_prompt = CompetingDiagnosisEngine.build_diagnostic_prompt(
            problem_statement=problem.problem_statement,
            behavior_map=behavior_map,
            candidate_profiles=candidate_profiles,
            failure_evidence=None,
            state_flows=state_flows,
            class_context=class_ctx_str or None,
            execution_path_summary=exec_path.format_for_prompt() if exec_path else "",
        )
        # Inject Unified Evidence Substrate
        unified_ev_summary = unified_evidence.format_summary_for_prompt()
        if unified_ev_summary:
            diag_prompt = diag_prompt + "\n" + unified_ev_summary + "\n"

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

        # Step 6: Dynamic Sandboxed Probing & Executable Counterfactuals (Optional)
        if allow_dynamic_probes and tester and len(diagnosis.hypotheses) >= 2:
            probe_runner = ExperimentRunner(repo_dir=repo_dir, tester=tester)
            target_probe_line = stage1_candidates[0].line_start
            if exec_path and exec_path.divergence_node:
                target_probe_line = exec_path.divergence_node.line_number
            exp_res = probe_runner.run_experiment(
                instance_id=problem.instance_id,
                diagnosis=diagnosis,
                target_file=stage1_candidates[0].file_path,
                target_symbol=stage1_candidates[0].symbol,
                target_line=target_probe_line,
            )
            result.experiment_executed = True
            result.experiment_summary = exp_res.summary
            telemetry_logger.log_event("EXPERIMENT", "PROBE_EXECUTED", details=exp_res.to_dict())

        # Step 7: Candidate Ranking & Selection
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

        cand_profiles_dict = {p.symbol: p for p in candidate_profiles}
        for p in candidate_profiles:
            cand_profiles_dict[p.symbol.split(".")[-1]] = p

        ranker = RepairSiteRanker(graph)
        ranked_sites = ranker.rank_sites(
            candidates=all_candidates,
            problem=problem,
            diagnosis=diagnosis,
            test_traceback=None,
            primary_file=candidate_file,
            behavior_map=behavior_map,
            candidate_profiles=cand_profiles_dict,
        )
        result.ranked_sites = ranked_sites

        selected_site = ranked_sites[0] if ranked_sites else stage1_candidates[0]
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
        active_hyp_idx = 0

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
            elif attempt == 2 and current_action != ReplanAction.FALLBACK_SYNTAX:
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
                )
                strategy_name = "refined_structured_json"
            elif attempt == 2 or (attempt == 3 and current_action == ReplanAction.FALLBACK_SYNTAX):
                prompt_to_use = StructuredRepairPromptBuilder.build_minimal_prompt(unit, error_feedback=last_error)
                strategy_name = "minimal_target_bound_json"
            else:
                prompt_to_use = StructuredRepairPromptBuilder.build_replacement_only_prompt(unit, error_feedback=last_error)
                strategy_name = "replacement_only_code"

            try:
                resp = self.provider.generate_one(prompt_to_use, system=SYSTEM_STRUCTURED_REPAIR_PROMPT)
                structured_out = StructuredRepairParser.parse(resp.text, unit)
            except Exception as e:
                last_error = str(e)
                continue

            # Deterministic Source Reconstruction
            reconstructed = SourceReconstructor.reconstruct_single(
                original_code=original_code,
                unit=unit,
                output=structured_out,
                file_rel_path=unit.file_path,
            )

            if not reconstructed.success:
                last_error = reconstructed.error
                continue

            # Write to disk temporarily for verification
            target_file_abs.write_text(reconstructed.modified_contents[unit.file_path], encoding="utf-8")

            diff_proc = subprocess.run(["git", "diff"], cwd=repo_dir, capture_output=True, text=True)
            candidate_diff = diff_proc.stdout or reconstructed.patch_text
            if req_matrix:
                req_matrix.evaluate(candidate_diff)

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
            verdict: AdaptiveVerificationVerdict = verifier.verify(
                instance_id=problem.instance_id,
                patch=applied_patch,
                original_sources={unit.file_path: original_code},
                reconstructed=reconstructed,
                target_units=[unit],
                contract=api_contract,
            )

            result.test_executed = verdict.tests_executed
            result.resolved = verdict.resolved
            result.failure_class = verdict.failure_class
            result.test_summary = verdict.test_summary
            result.validation_result = StaticRepairValidator.validate(
                original_sources={unit.file_path: original_code},
                reconstructed=reconstructed,
                target_units=[unit],
                contract=api_contract,
            )

            result.refinement_history.append({
                "attempt": attempt,
                "strategy": strategy_name,
                "static_valid": verdict.static_valid,
                "tests_executed": verdict.tests_executed,
                "resolved": verdict.resolved,
                "failure_class": verdict.failure_class,
                "error_message": verdict.error_message,
                "patch_text": applied_patch.patch_text if applied_patch else "",
            })

            if verdict.resolved:
                patch_applied = True
                result.patch = applied_patch
                result.patch_applied = True
                break

            # Failure-Driven Replanning
            decision = FailureDrivenReplanner.decide(
                failure_class=verdict.failure_class,
                attempt=attempt,
                max_attempts=max_repair_attempts,
                hypotheses_available=len(diagnosis.hypotheses),
                active_hypothesis_idx=active_hyp_idx,
                f2p_passed=verdict.f2p_passed,
                p2p_failed=verdict.p2p_total - verdict.p2p_passed if verdict.p2p_total else 0,
                error_message=verdict.error_message,
            )

            telemetry_logger.log_event("REPLAN", "FAILURE_REPLAN_DECISION", details=decision.__dict__)
            current_action = decision.action

            if decision.action == ReplanAction.SWITCH_HYPOTHESIS and decision.target_hypothesis_id:
                active_hyp_idx += 1
                if active_hyp_idx < len(diagnosis.hypotheses):
                    diagnosis.selected_hypothesis_idx = active_hyp_idx
                    diagnosis.selected_hypothesis = diagnosis.hypotheses[active_hyp_idx]

            last_error = decision.prompt_guidance or verdict.error_message
            _cleanup()

        result.patch = applied_patch
        result.patch_applied = patch_applied or (result.resolved)
        result.runtime_s = round(time.time() - t0, 2)
        result.telemetry = telemetry_logger.get_breakdown()

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
            save_repair_episode(episode)
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
