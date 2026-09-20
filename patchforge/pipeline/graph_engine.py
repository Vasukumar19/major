"""PatchForge Graph Reasoning & AST-Grounded Structured Repair Engine.

Coordinates:
Issue -> Repository Indexing -> Candidate Site Discovery ->
Evidence-Driven Repair Site Ranking -> AST-Grounded Repair Unit Planning ->
Compact Grounded Context Retrieval -> Behavioral Diagnosis (Enhanced) ->
Structured Model Repair -> Deterministic Source Reconstruction ->
Static Validation Gate -> Docker SWE-bench Execution ->
Graph-Aware Refinement -> Structured RepairEpisode Recording
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
from typing import Any, List, Optional, Tuple

from patchforge.core.target import EditSite, MultiSiteRepairTarget, RepairTarget
from patchforge.issue.problem import Problem
from patchforge.memory.episode import RepairEpisode, save_repair_episode
from patchforge.models.provider import ModelProvider, OllamaProvider
from patchforge.repair.patch import Patch
from patchforge.repair.planner import RepairUnitPlanner
from patchforge.repair.ranking import RepairSiteRanker
from patchforge.repair.reconstructor import SourceReconstructor
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
from patchforge.repository.map import RepoMap
from patchforge.repository_intelligence.diagnosis import (
    BehavioralDiagnosisEngine,
    DiagnosisResult,
)
from patchforge.repository_intelligence.flow import analyze_symbol_flow
from patchforge.repository_intelligence.graph import RepositoryGraph
from patchforge.repository_intelligence.indexer import RepositoryIndexer
from patchforge.repository_intelligence.refinement import (
    GraphAwareFailureRefiner,
    GraphRefinementEvidence,
)
from patchforge.repository_intelligence.retriever import (
    RepairContext,
    RepairContextRetriever,
)
from patchforge.repository_intelligence.schema import NodeKind
from patchforge.repository_intelligence.test_graph import TestGraphIndex
from patchforge.telemetry.events import TelemetryLogger
from patchforge.tools.editor import ApplyPatchTool
from patchforge.verification.classifier import FailureClass, classify
from patchforge.verification.tester import Tester

logger = logging.getLogger(__name__)

SYSTEM_DIAGNOSIS_PROMPT = """You are the Principal Software Diagnosis Engine for PatchForge AI.
Analyze the repository intelligence, structural graph, call hierarchy, control flow, and state transitions to provide a rigorous root-cause diagnosis.
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
class GraphEngineResult:
    instance_id: str
    target: RepairTarget | None = None
    diagnosis: DiagnosisResult | None = None
    patch: Patch | None = None
    patch_applied: bool = False
    test_executed: bool = False
    resolved: bool = False
    failure_class: str = FailureClass.UNRESOLVED.value
    runtime_s: float = 0.0
    telemetry: dict[str, Any] = field(default_factory=dict)
    test_summary: dict[str, Any] = field(default_factory=dict)
    refinement_cycles: int = 0
    refinement_history: list[dict[str, Any]] = field(default_factory=list)
    repair_unit: RepairUnit | None = None
    ranked_sites: list[RankedRepairSite] = field(default_factory=list)
    validation_result: StaticValidationResult | None = None


class GraphRepairEngine:
    """Coordinates repository intelligence, graph reasoning, diagnosis, and AST-grounded structured repair."""

    def __init__(
        self,
        provider: ModelProvider | None = None,
        model_name: str = "qwen2.5-coder:14b",
        workspace: str = ".",
        mode: str = "structured",  # "structured" (V0.6 default) or "diff" (V0.5 fallback)
    ):
        self.provider = provider or OllamaProvider(model=model_name)
        self.model_name = model_name
        self.workspace = workspace
        self.mode = mode

    def repair(
        self,
        problem: Problem,
        repo_dir: str,
        tester: Tester | None = None,
        max_retries: int = 3,
        max_refinement_cycles: int = 3,
        max_refinements: int | None = None,
    ) -> GraphEngineResult:
        """Executes full repository-intelligence & AST-grounded repair workflow."""
        if max_refinements is not None:
            max_refinement_cycles = max_refinements
        t0 = time.time()

        telemetry_logger = TelemetryLogger(problem.instance_id)
        result = GraphEngineResult(instance_id=problem.instance_id)
        repo_p = Path(repo_dir)

        def _cleanup():
            try:
                subprocess.run(["git", "checkout", "."], cwd=repo_dir, capture_output=True)
                subprocess.run(["git", "clean", "-fd"], cwd=repo_dir, capture_output=True)
            except Exception:
                pass

        _cleanup()

        # Step 1: Index Repository or load cached graph
        index_t0 = time.time()
        indexer = RepositoryIndexer(repo_dir)
        graph, test_index = indexer.index()
        index_ms = (time.time() - index_t0) * 1000.0
        telemetry_logger.log_event(
            "GRAPH", "INDEX_LOADED", duration_ms=index_ms,
            details={"nodes": len(graph.nodes), "edges": graph.g.number_of_edges()}
        )

        # Step 2: Fault Localization using RepoMap + Graph
        repo_map = RepoMap(repo_dir)
        issue_text = (problem.problem_statement or "") + "\n" + (problem.hints_text or "")

        py_files = [
            str(p.relative_to(repo_p)).replace("\\", "/")
            for p in repo_p.glob("**/*.py")
            if p.is_file() and not any(part in ("tests", "testing", "test", "docs", ".git", "venv", ".venv", "build", "dist") for part in p.parts)
        ]

        candidate_file = py_files[0] if py_files else "unknown.py"
        best_f_score = -1.0
        for pf in py_files:
            score = 0.0
            stem = Path(pf).stem.lower()
            if stem in issue_text.lower() and len(stem) > 3:
                score += 20.0
            symbols = repo_map.extract_symbols(pf)
            for s in symbols:
                if s.name.lower() in issue_text.lower() and len(s.name) > 3:
                    score += 10.0
            if score > best_f_score:
                best_f_score = score
                candidate_file = pf

        # Step 2: Stage 1 Candidate Site Discovery (bounded top 5-10)
        behavior_map = IssueBehaviorExtractor.extract(problem.problem_statement, getattr(problem, "hints_text", ""))
        symbols = repo_map.extract_symbols(candidate_file)

        # Collect candidate symbols
        candidate_syms_set: set[str] = set()
        for s in symbols:
            s_low = s.name.lower()
            if any(kw in s_low for kw in issue_text.lower().split() if len(kw) > 3) or s.name in behavior_map.mentioned_symbols:
                candidate_syms_set.add(s.name)
        # Class methods if class is located
        for s in symbols:
            if s.kind == "class":
                for m in symbols:
                    if m.kind in ("method", "function") and m.name != s.name:
                        candidate_syms_set.add(m.name)

        if not candidate_syms_set and symbols:
            candidate_syms_set.add(symbols[0].name)

        stage1_candidates: List[EditSite] = []
        for sym_name in list(candidate_syms_set)[:8]:
            s_src, w_start, w_end, b_ctx = repo_map.extract_surgical_unit(candidate_file, sym_name, issue_text)
            if s_src:
                stage1_candidates.append(
                    EditSite(
                        file_path=candidate_file,
                        symbol=sym_name,
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
            s_src, w_start, w_end, b_ctx = repo_map.extract_surgical_unit(candidate_file, symbols[0].name if symbols else "", issue_text)
            stage1_candidates.append(
                EditSite(
                    file_path=candidate_file,
                    symbol=symbols[0].name if symbols else "",
                    line_start=w_start,
                    line_end=w_end,
                    source_span=(w_start, w_end),
                    verified_source=s_src,
                    verification_status=bool(s_src),
                    behavior_context=b_ctx,
                    site_role="PRIMARY",
                )
            )

        # Step 3: Behavioral Evidence Engine Analysis
        evidence_engine = BehavioralEvidenceEngine(repo_dir=repo_dir, graph=graph, test_index=test_index)
        candidate_profiles: List[CandidateProfile] = []
        state_flows: dict[str, Any] = {}

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

        # Bounded large-class context if applicable
        class_ctx_str = ""
        for s in symbols:
            if s.kind == "class":
                bounded = evidence_engine.extract_bounded_class_context(
                    file_path=candidate_file,
                    class_name=s.name,
                    active_symbols=[c.symbol for c in stage1_candidates],
                )
                if bounded:
                    class_ctx_str = bounded.format_for_prompt()
                    break

        # Step 4: Competing Behavioral Diagnosis
        diag_t0 = time.time()
        diag_prompt = CompetingDiagnosisEngine.build_diagnostic_prompt(
            problem_statement=problem.problem_statement,
            behavior_map=behavior_map,
            candidate_profiles=candidate_profiles,
            failure_evidence=None,
            state_flows=state_flows,
            class_context=class_ctx_str,
        )
        try:
            diag_resp = self.provider.generate_one(diag_prompt, system=SYSTEM_DIAGNOSIS_PROMPT)
            diagnosis = CompetingDiagnosisEngine.parse_competing_diagnosis(
                response_text=diag_resp.text,
                candidate_profiles=candidate_profiles,
                behavior_map=behavior_map,
            )
            telemetry_logger.log_event(
                "DIAGNOSIS", "DIAGNOSIS_COMPLETED", duration_ms=(time.time() - diag_t0) * 1000.0,
                input_tokens=diag_resp.input_tokens, output_tokens=diag_resp.output_tokens,
                details={"cause_len": len(diagnosis.cause), "strategy_len": len(diagnosis.repair_strategy), "hyps_count": len(diagnosis.hypotheses)}
            )
        except Exception as e:
            logger.warning(f"Competing diagnosis generation failed: {e}")
            top_sym = stage1_candidates[0].symbol if stage1_candidates else "unknown"
            diagnosis = CompetingDiagnosisResult(
                hypotheses=[
                    DiagnosisHypothesis(
                        id="A",
                        cause="Identified divergence in target.",
                        invariant="Preserve callers.",
                        repair_strategy="Fix target behavior.",
                        affected_sites=[f"{candidate_file}:{top_sym}"],
                        confidence=0.8,
                    )
                ],
                selected_hypothesis_idx=0,
                repair_sites=[f"{candidate_file}:{top_sym}"],
                raw_response="",
            )
        result.diagnosis = diagnosis

        # Step 5: Stage 2 Candidate Site Evaluation & Ranking
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

        # Select top-ranked repair site as target
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
        target_file_rel = selected_site.file_path
        target_file_abs = repo_p / target_file_rel

        try:
            original_code = target_file_abs.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            logger.warning(f"Failed to read file {target_file_rel}: {e}")
            original_code = target.verified_source

        # Step 6: Graph Context Retrieval for Selected Target
        retriever = RepairContextRetriever(repo_dir, graph, test_index)
        repair_context = retriever.retrieve(
            problem_statement=problem.problem_statement,
            target_file=target.file_path,
            target_symbol=target.symbol,
        )

        # Step 7: AST-Grounded Repair Unit Planning
        unit = RepairUnitPlanner.plan_repair_unit(
            file_path=target_file_rel,
            source_code=original_code,
            target_symbol=selected_site.symbol,
            target_lines=(selected_site.line_start, selected_site.line_end),
            diagnosis=diagnosis,
            role=selected_site.role if hasattr(selected_site, "role") else "PRIMARY",
        )
        result.repair_unit = unit

        # Step 7: Structured Model Repair & Deterministic Reconstruction
        patch_applied = False
        applied_patch: Patch | None = None
        validation_res: StaticValidationResult | None = None

        repair_prompt = StructuredRepairPromptBuilder.build_repair_prompt(
            unit=unit,
            problem=problem,
            diagnosis=diagnosis,
            callers=[c.name for c in repair_context.callers[:4]] if repair_context else [],
            callees=[c.name for c in repair_context.callees[:4]] if repair_context else [],
            state_flow_summary=repair_context.state_flow or "",
            full_source=original_code,
        )

        last_error = ""
        repair_protocol_info: dict[str, Any] = {}

        for attempt in range(1, max_retries + 1):
            llm_t0 = time.time()
            if attempt == 1:
                prompt_to_use = repair_prompt
                strategy_name = "strict_structured_json"
            elif attempt == 2:
                prompt_to_use = StructuredRepairPromptBuilder.build_minimal_prompt(unit, error_feedback=last_error)
                strategy_name = "minimal_target_bound_json"
            else:
                prompt_to_use = StructuredRepairPromptBuilder.build_replacement_only_prompt(unit, error_feedback=last_error)
                strategy_name = "replacement_only_code"

            try:
                resp = self.provider.generate_one(prompt_to_use, system=SYSTEM_STRUCTURED_REPAIR_PROMPT)
                telemetry_logger.log_event(
                    "REPAIR", "STRUCTURED_LLM_GENERATE", duration_ms=(time.time() - llm_t0) * 1000.0,
                    input_tokens=resp.input_tokens, output_tokens=resp.output_tokens,
                    details={"attempt": attempt, "strategy": strategy_name}
                )
            except Exception as e:
                telemetry_logger.log_event("REPAIR", "LLM_ERROR", details={"error": str(e), "attempt": attempt, "strategy": strategy_name})
                continue

            # Parse structured output
            try:
                structured_out = StructuredRepairParser.parse(resp.text, unit)
            except Exception as e:
                last_error = str(e)
                telemetry_logger.log_event("REPAIR", "SCHEMA_PARSE_ERROR", details={"error": str(e), "attempt": attempt, "strategy": strategy_name})
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
                telemetry_logger.log_event("REPAIR", "RECONSTRUCTION_FAILED", details={"error": reconstructed.error, "attempt": attempt, "strategy": strategy_name})
                continue

            # Static Validation Gate
            validation_res = StaticRepairValidator.validate(
                original_sources={unit.file_path: original_code},
                reconstructed=reconstructed,
                target_units=[unit],
            )
            result.validation_result = validation_res

            if not validation_res.valid:
                last_error = "; ".join(validation_res.errors)
                telemetry_logger.log_event("REPAIR", "VALIDATION_FAILED", details={"errors": validation_res.errors, "attempt": attempt, "strategy": strategy_name})
                continue

            # Write reconstructed source to disk
            try:
                target_file_abs.write_text(reconstructed.modified_contents[unit.file_path], encoding="utf-8")
                patch_applied = True
                applied_patch = Patch(
                    hypothesis_id="STRUCTURED_H1",
                    files_changed=reconstructed.files_changed,
                    symbols_changed=reconstructed.symbols_changed,
                    patch_text=reconstructed.patch_text,
                    valid=True,
                    match_tier="AST_GROUNDED",
                    input_tokens=resp.input_tokens,
                    output_tokens=resp.output_tokens,
                    new_contents=reconstructed.modified_contents,
                )
                repair_protocol_info = {
                    "attempts": attempt,
                    "strategy": strategy_name,
                    "target_unit_id": unit.id,
                    "target_unit_type": unit.unit_type.value,
                    "repair_action": structured_out.repair_action,
                    "accepted": True,
                }
                telemetry_logger.log_event("APPLY", "STRUCTURED_PATCH_APPLIED", details={"diff_len": len(applied_patch.patch_text), "strategy": strategy_name})
                break
            except Exception as e:
                telemetry_logger.log_event("APPLY", "WRITE_DISK_FAILED", details={"error": str(e)})

        result.patch = applied_patch
        result.patch_applied = patch_applied

        if not patch_applied or not applied_patch:
            if validation_res and not validation_res.valid:
                result.failure_class = FailureClass.REPAIR_VALIDATION_FAILURE.value
            else:
                result.failure_class = FailureClass.REPAIR_SCHEMA_FAILURE.value
            result.runtime_s = round(time.time() - t0, 2)
            breakdown = telemetry_logger.get_breakdown()
            breakdown["repair_protocol"] = repair_protocol_info or {
                "attempts": max_retries,
                "last_error": last_error,
                "accepted": False,
            }
            result.telemetry = breakdown
            _cleanup()
            return result

        # Step 8: Test Execution & Verification
        if tester:
            test_t0 = time.time()
            verdict = tester.run(problem.instance_id, applied_patch.patch_text)
            result.test_executed = True
            result.resolved = bool(getattr(verdict, "resolved", False))
            f_class = classify(applied_patch, verdict)
            result.failure_class = f_class.value
            result.test_summary = verdict.to_dict() if hasattr(verdict, "to_dict") else {}
            telemetry_logger.log_event(
                "TEST", "TEST_EXECUTION", duration_ms=(time.time() - test_t0) * 1000.0,
                details={"resolved": result.resolved, "f2p_passed": getattr(verdict, "fail_to_pass_passed", 0)}
            )

            # Step 9: Graph-Aware Refinement Loop
            refiner = GraphAwareFailureRefiner(repo_dir, graph, test_index)
            failure_trace = getattr(verdict, "error", "") or str(result.test_summary)

            ref_cycle = 0
            while not result.resolved and ref_cycle < max_refinement_cycles:
                ref_cycle += 1
                result.refinement_cycles = ref_cycle
                telemetry_logger.log_event("REFINEMENT", "CYCLE_START", details={"cycle": ref_cycle})

                evidence = refiner.analyze_failure(
                    primary_symbol=unit.symbol,
                    primary_file=unit.file_path,
                    failure_traceback=failure_trace,
                )

                # If an additional secondary site is suggested, re-plan repair unit
                ref_unit = unit
                ref_file = unit.file_path
                ref_source = original_code

                if evidence.suggested_additional_site and ":" in evidence.suggested_additional_site:
                    s_file, s_sym = evidence.suggested_additional_site.split(":", 1)
                    s_file_abs = repo_p / s_file
                    if s_file_abs.exists():
                        ref_file = s_file
                        ref_source = s_file_abs.read_text(encoding="utf-8", errors="replace")
                        ref_unit = RepairUnitPlanner.plan_repair_unit(
                            file_path=s_file,
                            source_code=ref_source,
                            target_symbol=s_sym,
                            diagnosis=diagnosis,
                            role="TRACE_DERIVED",
                        )

                # Re-synthesize with compact refinement prompt
                ref_prompt = refiner.build_refinement_prompt(
                    problem_statement=problem.problem_statement,
                    repair_context=repair_context,
                    evidence=evidence,
                    previous_patch=applied_patch.patch_text,
                )
                ref_prompt += "\n" + StructuredRepairPromptBuilder.build_repair_prompt(
                    unit=ref_unit,
                    problem=problem,
                    diagnosis=diagnosis,
                    full_source=ref_source,
                )

                try:
                    ref_resp = self.provider.generate_one(ref_prompt, system=SYSTEM_STRUCTURED_REPAIR_PROMPT)
                    ref_struct = StructuredRepairParser.parse(ref_resp.text, ref_unit)
                except Exception as e:
                    logger.warning(f"Refinement LLM or parse failed: {e}")
                    break

                _cleanup()
                ref_reconstructed = SourceReconstructor.reconstruct_single(
                    original_code=ref_source,
                    unit=ref_unit,
                    output=ref_struct,
                    file_rel_path=ref_unit.file_path,
                )

                if not ref_reconstructed.success:
                    break

                ref_val = StaticRepairValidator.validate(
                    original_sources={ref_unit.file_path: ref_source},
                    reconstructed=ref_reconstructed,
                    target_units=[ref_unit],
                )
                if not ref_val.valid:
                    break

                (repo_p / ref_unit.file_path).write_text(
                    ref_reconstructed.modified_contents[ref_unit.file_path], encoding="utf-8"
                )

                diff_proc = subprocess.run(["git", "diff"], cwd=repo_dir, capture_output=True, text=True)
                ref_full_diff = diff_proc.stdout or ref_reconstructed.patch_text

                applied_patch = Patch(
                    hypothesis_id=f"STRUCTURED_REF_{ref_cycle}",
                    files_changed=ref_reconstructed.files_changed,
                    symbols_changed=ref_reconstructed.symbols_changed,
                    patch_text=ref_full_diff,
                    valid=True,
                    match_tier="AST_GROUNDED",
                    input_tokens=ref_resp.input_tokens,
                    output_tokens=ref_resp.output_tokens,
                )
                result.patch = applied_patch
                verdict = tester.run(problem.instance_id, applied_patch.patch_text)
                result.resolved = bool(getattr(verdict, "resolved", False))
                f_class = classify(applied_patch, verdict)
                result.failure_class = f_class.value
                result.test_summary = verdict.to_dict() if hasattr(verdict, "to_dict") else {}
                failure_trace = getattr(verdict, "error", "") or str(result.test_summary)

                result.refinement_history.append({
                    "cycle": ref_cycle,
                    "resolved": result.resolved,
                    "f2p_passed": getattr(verdict, "fail_to_pass_passed", 0),
                    "evidence": evidence.__dict__,
                })
                if result.resolved:
                    break

        result.runtime_s = round(time.time() - t0, 2)
        result.telemetry = telemetry_logger.get_breakdown()

        # Step 10: Save Structured Repair Episode
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
                graph_context={
                    "nodes_count": len(graph.nodes),
                    "edges_count": graph.g.number_of_edges(),
                    "callers": [c.name for c in repair_context.callers] if repair_context else [],
                    "callees": [c.name for c in repair_context.callees] if repair_context else [],
                },
                diagnosis=diagnosis.to_dict() if hasattr(diagnosis, "to_dict") else diagnosis.__dict__,
                causal_chain=[diagnosis.repair_strategy] if diagnosis else [],
                state_flow_summary=repair_context.state_flow or "" if repair_context else "",
                resolved=result.resolved,
                patch_applied=result.patch_applied,
                patch_valid=bool(result.patch and result.patch.valid),
                failure_class=result.failure_class,
                runtime_s=result.runtime_s,
                f2p_passed=f2p_p,
                f2p_total=f2p_t,
                p2p_passed=p2p_p,
                p2p_total=p2p_t,
                refinement_cycles=result.refinement_cycles,
                refinement_history=result.refinement_history,
                patch_text=applied_patch.patch_text if applied_patch else "",
                test_summary=result.test_summary,
                created_at=time.strftime("%Y-%m-%d %H:%M:%S"),
                repair_unit=unit.to_dict() if unit else {},
                ranked_candidates=[s.to_dict() for s in ranked_sites[:5]],
                validation_result=result.validation_result.to_dict() if result.validation_result else {},
            )
            save_repair_episode(episode)
        except Exception as e:
            logger.warning(f"Failed to record repair episode: {e}")

        _cleanup()
        return result

    run = repair
