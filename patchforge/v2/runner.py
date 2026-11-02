"""V2RepairRunner — canonical end-to-end repair orchestrator using V2 infrastructure.

V2 replaces shared mutable repair attempts with:
  1. Isolated copy-on-write workspaces (WorkspaceManager & CandidateWorkspaceService)
  2. Append-only candidate ledger with complete lineage (CandidateLedger)
  3. Evidence-based multi-attribute candidate ranking
  4. Real container-backed targeted residual testing (DockerSelectedTestExecutor)
  5. Assertion-level failure extraction feeding iterative refinement
  6. Comprehensive, reproducible JSON & Markdown repair traces

Design principles
-----------------
- No hard-coded Ollama URLs, Docker commands, or image names.
- Configuration boundary is PATCHFORGE_OLLAMA_URL & PATCHFORGE_DOCKER_COMMAND.
- Never mutates the caller's repository checkout or resets parent workspaces.
- Final SWE-bench harness evaluation is required for declaring task resolution.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from patchforge.core.target import EditSite, RepairTarget
from patchforge.diagnosis import (
    BehavioralEvidenceEngine,
    CandidateProfile,
    CompetingDiagnosisEngine,
    CompetingDiagnosisResult,
    DiagnosisHypothesis,
    IssueBehaviorExtractor,
)
from patchforge.diagnosis.contract import ContractExtractor
from patchforge.diagnosis.contract_gate import (
    BehavioralRequirementMatrix,
    HypothesisContractGate,
)
from patchforge.diagnosis.evidence import EvidenceKind, EvidencePackage
from patchforge.diagnosis.unified_evidence import UnifiedEvidenceEngine
from patchforge.models.provider import ModelProvider, OllamaProvider
from patchforge.repair.assertion_failures import AssertionFailureExtractor
from patchforge.repair.planner import RepairUnitPlanner
from patchforge.repair.ranking import RepairSiteRanker
from patchforge.repair.reconstructor import SourceReconstructor
from patchforge.repair.replanner import FailureDrivenReplanner, ReplanAction
from patchforge.repair.residual import ResidualFailureRecord
from patchforge.repair.schema import RepairUnit, StructuredRepairOutput
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
from patchforge.runtime import RuntimeEnvironment
from patchforge.verification.classifier import FailureClass

from .candidates import Candidate, CandidateLedger, CandidateStatus, TestObservation
from .docker_executor import DockerSelectedTestExecutor, SELECTED_TEST_UNAVAILABLE
from .execution import CandidateWorkspaceService
from .report import CandidateRecord, V2RepairReport
from .verification import SelectedTestExecutor, accepts_residual_candidate
from .workspaces import OwnedWorkspace, WorkspaceManager

logger = logging.getLogger(__name__)

_DEFAULT_RESULTS_DIR = Path("results") / "v2_pilot"

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
class V2RunnerConfig:
    """All tunable knobs for a V2 repair run — sourced from env or caller."""

    model: str = "qwen2.5-coder:14b"
    ollama_url: str = ""
    docker_image: str = ""
    docker_command: str = ""
    harness_timeout_seconds: int = 300
    max_refinement_cycles: int = 3
    workspaces_root: str = ""
    results_dir: str = ""
    allow_host_execution: bool = False

    @classmethod
    def from_environment(cls, **overrides: Any) -> "V2RunnerConfig":
        env = RuntimeEnvironment.from_environment()
        raw_timeout = os.getenv("SWEBENCH_HARNESS_TIMEOUT", "300")
        try:
            timeout = max(1, int(raw_timeout))
        except ValueError:
            timeout = 300
        cfg = cls(
            model=os.getenv("PATCHFORGE_MODEL", "qwen2.5-coder:14b"),
            ollama_url=env.ollama_url or "",
            docker_command=env.docker_command or "",
            docker_image=os.getenv("PATCHFORGE_DOCKER_IMAGE", ""),
            harness_timeout_seconds=timeout,
        )
        for k, v in overrides.items():
            if hasattr(cfg, k):
                setattr(cfg, k, v)
        return cfg


class V2RepairRunner:
    """Canonical V2 software-repair orchestrator."""

    def __init__(
        self,
        config: V2RunnerConfig | None = None,
        provider: ModelProvider | None = None,
        tester: Any = None,
        selected_test_executor: SelectedTestExecutor | None = None,
    ):
        self.config = config or V2RunnerConfig.from_environment()
        self.provider = provider or OllamaProvider(
            model=self.config.model,
            base_url=self.config.ollama_url or None,
        )
        self.tester = tester
        self.selected_test_executor = selected_test_executor

    def run_task(
        self,
        instance_id: str,
        repo_dir: str | Path,
        *,
        problem: Any,
        test_selectors: list[str] | None = None,
        results_dir: str | Path | None = None,
        tester: Any = None,
    ) -> V2RepairReport:
        """Execute the full V2 repair lifecycle on an isolated copy of repo_dir."""
        cfg = self.config
        active_tester = tester or self.tester
        report = V2RepairReport(
            instance_id=instance_id,
            base_revision=_safe_git_revision(repo_dir),
            model=cfg.model,
            ollama_url=cfg.ollama_url,
            docker_image=cfg.docker_image,
        )

        # 0. Runtime validation
        env = RuntimeEnvironment(
            ollama_url=cfg.ollama_url or None,
            docker_command=cfg.docker_command or None,
        )
        ollama_probe = env.probe_ollama()
        docker_probe = env.probe_docker()

        if not ollama_probe.ready:
            msg = f"Ollama not ready: {ollama_probe.detail}"
            report.notes.append(msg)
            logger.error(msg)
            report.finish()
            self._save_report(report, results_dir)
            return report

        report.notes.append(f"Ollama: {ollama_probe.detail}")
        if docker_probe.ready:
            report.notes.append(f"Docker: {docker_probe.detail}")
        else:
            report.notes.append(f"Docker unavailable: {docker_probe.detail}")

        # Determine target test selectors (from problem or argument)
        effective_selectors = list(test_selectors or [])
        if not effective_selectors and hasattr(problem, "fail_to_pass") and problem.fail_to_pass:
            effective_selectors = [str(s) for s in problem.fail_to_pass]
        elif not effective_selectors and hasattr(problem, "FAIL_TO_PASS") and problem.FAIL_TO_PASS:
            effective_selectors = [str(s) for s in problem.FAIL_TO_PASS]

        # Determine executor
        executor: SelectedTestExecutor | None = self.selected_test_executor
        if not executor and docker_probe.ready:
            image = cfg.docker_image or f"swebench/sweb.eval.x86_64.{instance_id.replace('__', '_1776_')}:latest"
            executor = DockerSelectedTestExecutor(
                image_tag=image,
                timeout_seconds=cfg.harness_timeout_seconds,
            )
            report.docker_image = image

        # 1. Setup V2 Workspace & Candidate Ledger
        workspaces_root = Path(
            cfg.workspaces_root
            or os.getenv("PATCHFORGE_WORKSPACES_DIR", "")
            or "results/v2_workspaces"
        ).resolve()
        manager = WorkspaceManager(workspaces_root / instance_id)
        ledger_path = workspaces_root / instance_id / "candidates.jsonl"
        ledger = CandidateLedger(ledger_path)
        service = CandidateWorkspaceService(manager, ledger)

        try:
            base_workspace = service.create_root(repo_dir, report.base_revision)
            report.notes.append(f"Base workspace created: {base_workspace.root}")
        except Exception as exc:
            report.notes.append(f"Workspace creation failed: {exc}")
            report.finish()
            self._save_report(report, results_dir)
            return report

        base_root = base_workspace.root

        # 2. Repository Intelligence & Competing Diagnosis (on base_root)
        try:
            indexer = RepositoryIndexer(str(base_root))
            graph, test_index = indexer.index()
            temporal_graph = TemporalRepositoryGraph(str(base_root), code_graph=graph).build(max_commits=30)

            issue_text = (getattr(problem, "problem_statement", "") or "") + "\n" + (getattr(problem, "hints_text", "") or "")
            behavior_map = IssueBehaviorExtractor.extract(
                getattr(problem, "problem_statement", "") or "",
                getattr(problem, "hints_text", "") or "",
            )
            api_contract = ContractExtractor.extract_contract(
                getattr(problem, "problem_statement", "") or "",
                getattr(problem, "hints_text", "") or "",
            )
            req_matrix = BehavioralRequirementMatrix.decompose(
                getattr(problem, "problem_statement", "") or "",
                getattr(problem, "hints_text", "") or "",
                contract=api_contract,
            )

            retriever = HybridRepositoryRetriever(
                repo_dir=str(base_root),
                code_graph=graph,
                test_index=test_index,
                temporal_graph=temporal_graph,
            )
            retrieved_cands = retriever.retrieve(
                query=getattr(problem, "problem_statement", "") or "",
                hints_text=getattr(problem, "hints_text", "") or "",
                traceback_text=None,
                top_k=8,
            )

            repo_map = RepoMap(str(base_root))
            candidate_file = retrieved_cands[0].file_path if retrieved_cands else "unknown.py"
            stage1_candidates: list[EditSite] = []
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

            evidence_engine = BehavioralEvidenceEngine(repo_dir=str(base_root), graph=graph, test_index=test_index)
            unified_evidence = UnifiedEvidenceEngine(code_graph=graph, temporal_graph=temporal_graph)
            candidate_profiles: list[CandidateProfile] = []
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

                t_prof = temporal_graph.get_symbol_profile(c.file_path, c.symbol)
                unified_evidence.aggregate_candidate_evidence(
                    profile=prof,
                    behavior_map=behavior_map,
                    temporal_profile=t_prof,
                    state_flow=s_flow,
                )

            ev_package = EvidencePackage(
                instance_id=instance_id,
                problem_statement=getattr(problem, "problem_statement", "") or "",
                expected_behavior=behavior_map.expected_behavior if behavior_map else "",
            )
            ev_package.candidate_symbols = [f"{c.file_path}:{c.symbol}" for c in stage1_candidates]

            diag_prompt = CompetingDiagnosisEngine.build_diagnostic_prompt(
                problem_statement=getattr(problem, "problem_statement", "") or "",
                behavior_map=behavior_map,
                candidate_profiles=candidate_profiles,
                failure_evidence=ev_package,
                state_flows=state_flows,
            )
            diag_resp = self.provider.generate_one(diag_prompt, system=SYSTEM_DIAGNOSIS_PROMPT)
            diagnosis = CompetingDiagnosisEngine.parse_competing_diagnosis(
                response_text=diag_resp.text,
                candidate_profiles=candidate_profiles,
                behavior_map=behavior_map,
            )
            diagnosis = HypothesisContractGate.evaluate_and_filter(diagnosis, api_contract, req_matrix)

            report.diagnosis_hypothesis = getattr(diagnosis.selected_hypothesis, "cause", "") or ""
            report.diagnosis_confidence = float(getattr(diagnosis.selected_hypothesis, "confidence", 0.0) or 0.0)
            report.competing_hypotheses = [
                {"id": h.id, "cause": h.cause, "repair_strategy": h.repair_strategy, "confidence": h.confidence}
                for h in diagnosis.hypotheses
            ]

            # Reconcile / rank targets
            all_candidates = list(stage1_candidates)
            cand_profiles_dict = {f"{p.file_path}:{p.symbol}": p for p in candidate_profiles if hasattr(p, "file_path")}
            ranker = RepairSiteRanker(graph)
            ranked_sites = ranker.rank_sites(
                candidates=all_candidates,
                problem=problem,
                diagnosis=diagnosis,
                candidate_profiles=cand_profiles_dict,
            )
            selected_site = ranked_sites[0] if ranked_sites else stage1_candidates[0]
            report.localized_files = list(set([s.file_path for s in (ranked_sites or stage1_candidates)]))

        except Exception as exc:
            report.notes.append(f"Diagnosis / intelligence error: {exc}")
            logger.exception("Diagnosis phase error")
            report.finish()
            self._save_report(report, results_dir)
            return report

        # 3. Candidate Refinement Loop
        active_parent_ws = base_workspace
        active_parent_cand_id: str | None = None
        active_hyp_idx = getattr(diagnosis, "selected_hypothesis_idx", 0)
        attempt = 0
        max_attempts = cfg.max_refinement_cycles

        seen_patch_hashes: set[str] = set()
        seen_failure_signatures: set[str] = set()
        last_residual_guidance = ""
        last_prior_patch_code = ""
        last_error_feedback = ""
        last_residual_record: ResidualFailureRecord | None = None

        while attempt < max_attempts:
            attempt += 1
            logger.info("Starting V2 repair attempt %d / %d for %s", attempt, max_attempts, instance_id)

            # Resolve current target file & source from the active parent workspace
            target_file_rel = selected_site.file_path
            target_file_abs = active_parent_ws.root / target_file_rel
            try:
                original_code = target_file_abs.read_text(encoding="utf-8", errors="replace")
            except OSError:
                original_code = selected_site.verified_source or ""

            context_retriever = RepairContextRetriever(str(active_parent_ws.root), graph, test_index)
            repair_context = context_retriever.retrieve(
                problem_statement=getattr(problem, "problem_statement", "") or "",
                target_file=target_file_rel,
                target_symbol=selected_site.symbol,
            )

            unit = RepairUnitPlanner.plan_repair_unit(
                file_path=target_file_rel,
                source_code=original_code,
                target_symbol=selected_site.symbol,
                target_lines=(selected_site.line_start, selected_site.line_end),
                diagnosis=diagnosis,
            )

            # Build repair prompt
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
            else:
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
                    error_feedback=last_error_feedback,
                    residual_guidance=last_residual_guidance,
                    prior_patch_code=last_prior_patch_code,
                )

            # Generate replacement
            try:
                resp = self.provider.generate_one(prompt_to_use, system=SYSTEM_STRUCTURED_REPAIR_PROMPT)
                structured_out = StructuredRepairParser.parse(resp.text, unit)
            except Exception as exc:
                err_msg = str(exc)
                logger.warning("Generation/parser failure in attempt %d: %s", attempt, err_msg)
                last_error_feedback = f"PARSER ERROR: {err_msg}"
                decision = FailureDrivenReplanner.decide(
                    failure_class=FailureClass.PATCH_SYNTAX.value,
                    attempt=attempt,
                    max_attempts=max_attempts,
                    error_message=err_msg,
                    residual_record=last_residual_record,
                )
                report.replanning_decisions.append(asdict(decision))
                if decision.action == ReplanAction.ABORT:
                    break
                continue

            # Reconstruct code
            try:
                reconstructed = SourceReconstructor.reconstruct_single(
                    original_code=original_code,
                    unit=unit,
                    output=structured_out,
                    file_rel_path=unit.file_path,
                )
            except Exception as exc:
                logger.warning("Reconstruction exception in attempt %d: %s", attempt, exc)
                last_error_feedback = f"RECONSTRUCTION ERROR: {exc}"
                continue

            if not reconstructed.success:
                rec_err = reconstructed.error or "Reconstruction produced no changes"
                last_error_feedback = f"RECONSTRUCTION FAILED: {rec_err}"
                decision = FailureDrivenReplanner.decide(
                    failure_class=FailureClass.REPAIR_VALIDATION_FAILURE.value,
                    attempt=attempt,
                    max_attempts=max_attempts,
                    error_message=rec_err,
                    residual_record=last_residual_record,
                )
                report.replanning_decisions.append(asdict(decision))
                if decision.action == ReplanAction.ABORT:
                    break
                continue

            patch_diff = reconstructed.patch_text
            patch_hash = hashlib.sha256(patch_diff.encode("utf-8")).hexdigest()[:16]

            # Loop breaker: duplicate patch detection
            if patch_hash in seen_patch_hashes:
                logger.warning("Duplicate patch hash %s detected in attempt %d", patch_hash, attempt)
                report.notes.append(f"Attempt {attempt}: duplicate patch {patch_hash} skipped")
                decision = FailureDrivenReplanner.decide(
                    failure_class=FailureClass.UNRESOLVED.value,
                    attempt=attempt,
                    max_attempts=max_attempts,
                    repeated_attempt=True,
                    error_message="Duplicate patch hash proposed",
                    available_hypotheses=diagnosis.hypotheses,
                    active_hypothesis_idx=active_hyp_idx,
                )
                report.replanning_decisions.append(asdict(decision))
                if decision.action == ReplanAction.SWITCH_HYPOTHESIS and decision.target_hypothesis_id:
                    active_hyp_idx = (active_hyp_idx + 1) % len(diagnosis.hypotheses)
                    diagnosis.set_active_hypothesis(active_hyp_idx)
                elif decision.action == ReplanAction.ABORT:
                    break
                continue

            seen_patch_hashes.add(patch_hash)

            # Create candidate child workspace
            try:
                candidate = service.create_candidate(
                    active_parent_ws,
                    base_revision=report.base_revision,
                    patch_text=patch_diff,
                    changed_contents=reconstructed.modified_contents,
                    rationale=structured_out.reasoning or f"Attempt {attempt} repair",
                    hypothesis_id=diagnosis.hypotheses[active_hyp_idx].id if active_hyp_idx < len(diagnosis.hypotheses) else "A",
                    model_metadata={"attempt": attempt, "model": cfg.model, "hash": patch_hash},
                    parent_candidate_id=active_parent_cand_id,
                )
            except Exception as exc:
                logger.error("Failed to create candidate child workspace: %s", exc)
                report.notes.append(f"Attempt {attempt}: workspace creation error: {exc}")
                continue

            cand_ws = service.workspace_for(candidate)

            # Static Validation Gate
            val_res = StaticRepairValidator.validate(
                original_sources={unit.file_path: original_code},
                reconstructed=reconstructed,
                target_units=[unit],
                contract=api_contract,
            )
            if not val_res.valid:
                val_err = "; ".join(val_res.errors)
                service.ledger.reject(candidate.candidate_id, f"Static validation rejected: {val_err}")
                last_error_feedback = f"VALIDATION ERROR: {val_err}"
                decision = FailureDrivenReplanner.decide(
                    failure_class=FailureClass.REPAIR_VALIDATION_FAILURE.value,
                    attempt=attempt,
                    max_attempts=max_attempts,
                    error_message=val_err,
                    residual_record=last_residual_record,
                )
                report.replanning_decisions.append(asdict(decision))
                _append_candidate_record(report, candidate, obs=None, guidance=last_residual_guidance)
                continue

            # Targeted Test Execution
            obs: TestObservation | None = None
            if executor and effective_selectors:
                try:
                    obs = executor.run_selected(cand_ws, effective_selectors, cfg.harness_timeout_seconds)
                    service.observe(candidate, obs)

                    # Extract assertion failures
                    failures = AssertionFailureExtractor.extract(obs.output, fallback_test_ids=list(obs.failed))
                    service.ledger.record_evidence(candidate.candidate_id, [asdict(f) for f in failures])

                    # Build residual failure record
                    res_rec = ResidualFailureRecord(
                        instance_id=instance_id,
                        attempt=attempt,
                        f2p_passed_tests=list(obs.passed),
                        f2p_failed_tests=list(obs.failed),
                        f2p_passed_count=len(obs.passed),
                        f2p_total_count=len(effective_selectors),
                        residual_error_messages=[obs.output[-500:]] if obs.failed else [],
                        failing_assertions=failures,
                        is_partial_progress=bool(obs.passed) and not bool(obs.timed_out),
                    )
                    last_residual_record = res_rec
                    last_residual_guidance = res_rec.format_corrective_guidance()

                    # Acceptance policy: F2P progress and clean P2P
                    if accepts_residual_candidate(obs):
                        service.ledger.accept(candidate.candidate_id)
                        active_parent_ws = cand_ws
                        active_parent_cand_id = candidate.candidate_id
                        last_prior_patch_code = patch_diff
                        report.f2p_improvement = True
                        report.notes.append(f"Attempt {attempt}: Candidate {candidate.candidate_id} accepted ({len(obs.passed)} passed)")
                    else:
                        service.ledger.reject(candidate.candidate_id, f"Targeted tests: {len(obs.failed)} failed, {len(obs.passed)} passed")
                        report.notes.append(f"Attempt {attempt}: Candidate {candidate.candidate_id} rejected (no F2P progress)")

                except Exception as exc:
                    logger.exception("Selected-test execution error in attempt %d", attempt)
                    report.notes.append(f"Attempt {attempt}: test execution exception: {exc}")
            else:
                # No isolated executor configured or no selectors: accept candidate provisionally
                service.ledger.accept(candidate.candidate_id)
                active_parent_ws = cand_ws
                active_parent_cand_id = candidate.candidate_id
                last_prior_patch_code = patch_diff
                report.notes.append(f"Attempt {attempt}: Candidate {candidate.candidate_id} accepted provisionally (no executor/selectors)")

            _append_candidate_record(report, candidate, obs=obs, guidance=last_residual_guidance)

            # Failure signature loop breaker
            sig = f"{unit.file_path}:{unit.symbol}:{obs.exit_code if obs else 'no_test'}:{len(obs.failed) if obs else 0}"
            is_repeated_sig = sig in seen_failure_signatures
            seen_failure_signatures.add(sig)

            # Replanning decision
            fail_class = FailureClass.RESOLVED.value if (obs and not obs.failed and not obs.timed_out) else FailureClass.TEST_FAILURE.value
            decision = FailureDrivenReplanner.decide(
                failure_class=fail_class,
                attempt=attempt,
                max_attempts=max_attempts,
                f2p_passed=len(obs.passed) if obs else 0,
                p2p_failed=0,
                error_message=obs.output[-400:] if obs else "",
                residual_record=last_residual_record,
                repeated_attempt=is_repeated_sig,
                available_hypotheses=diagnosis.hypotheses,
                active_hypothesis_idx=active_hyp_idx,
            )
            report.replanning_decisions.append(asdict(decision))

            if decision.action == ReplanAction.ABORT:
                report.notes.append(f"Attempt {attempt}: Replanner requested ABORT ({decision.reason})")
                break
            elif decision.action == ReplanAction.SWITCH_HYPOTHESIS and decision.target_hypothesis_id:
                active_hyp_idx = (active_hyp_idx + 1) % len(diagnosis.hypotheses)
                diagnosis.set_active_hypothesis(active_hyp_idx)
                report.notes.append(f"Attempt {attempt}: Switched to Hypothesis {diagnosis.hypotheses[active_hyp_idx].id}")

        # 4. Candidate Ranking and Promotion
        ranked_candidates = service.ledger.rank_candidates()
        best_cand = service.ledger.best_accepted() or (ranked_candidates[0] if ranked_candidates else None)

        if best_cand:
            report.best_candidate_id = best_cand.candidate_id
            logger.info("Best candidate selected: %s (score=%.2f)", best_cand.candidate_id, best_cand.ranking_score)

            # 5. Final SWE-bench Verification (only on the best candidate)
            if active_tester and hasattr(active_tester, "run"):
                try:
                    logger.info("Running final SWE-bench evaluation on best candidate %s", best_cand.candidate_id)
                    eval_verdict = active_tester.run(instance_id, patch_text=best_cand.patch_text)
                    verdict_dict = eval_verdict.to_dict() if hasattr(eval_verdict, "to_dict") else {}
                    service.ledger.record_verdict(best_cand.candidate_id, verdict_dict)

                    report.resolved = bool(getattr(eval_verdict, "resolved", False))
                    ts = verdict_dict.get("test_summary", {}) or verdict_dict
                    f2p = ts.get("fail_to_pass", {})
                    p2p = ts.get("pass_to_pass", {})
                    report.harness_f2p_passed = int(f2p.get("passed", 0)) if isinstance(f2p, dict) else int(ts.get("fail_to_pass_passed", 0) or 0)
                    report.harness_f2p_total = int(f2p.get("total", 0)) if isinstance(f2p, dict) else int(ts.get("fail_to_pass_total", 0) or 0)
                    report.harness_p2p_passed = int(p2p.get("passed", 0)) if isinstance(p2p, dict) else int(ts.get("pass_to_pass_passed", 0) or 0)
                    report.harness_p2p_total = int(p2p.get("total", 0)) if isinstance(p2p, dict) else int(ts.get("pass_to_pass_total", 0) or 0)

                    if report.resolved:
                        service.ledger.accept(best_cand.candidate_id, final=True)
                        report.notes.append(f"Official SWE-bench verification CONFIRMED RESOLVED for {best_cand.candidate_id}")
                    else:
                        report.notes.append(f"Official SWE-bench verdict: F2P {report.harness_f2p_passed}/{report.harness_f2p_total}")

                except Exception as exc:
                    logger.exception("Final official evaluation error")
                    report.notes.append(f"Official evaluation error: {exc}")

        # Update candidate records in report with latest ledger state
        for cand in report.candidates:
            live = service.ledger.get_candidate(cand.candidate_id)
            if live:
                cand.status = live.status.value
                cand.ranking_score = live.ranking_score
                cand.rejection_reason = live.rejection_reason

        report.finish()
        self._save_report(report, results_dir)
        return report

    def _save_report(
        self,
        report: V2RepairReport,
        results_dir: str | Path | None,
    ) -> None:
        dest_dir = Path(
            results_dir
            or self.config.results_dir
            or _DEFAULT_RESULTS_DIR
        )
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_json = dest_dir / f"{report.instance_id}.json"
        dest_trace = dest_dir / f"{report.instance_id}_trace.md"
        try:
            report.save(dest_json)
            dest_trace.write_text(report.to_markdown_trace(), encoding="utf-8")
            logger.info("V2RepairReport saved to %s and %s", dest_json, dest_trace)
        except Exception as exc:
            logger.error("Failed to save V2RepairReport: %s", exc)


def _safe_git_revision(repo_dir: str | Path) -> str:
    import subprocess
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_dir), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        if proc.returncode == 0:
            return proc.stdout.strip()
    except Exception:
        pass
    return "unknown"


def _append_candidate_record(
    report: V2RepairReport,
    candidate: Candidate,
    obs: TestObservation | None,
    guidance: str = "",
) -> None:
    rec = CandidateRecord(
        candidate_id=candidate.candidate_id,
        parent_candidate_id=candidate.parent_candidate_id,
        base_revision=candidate.base_revision,
        patch_text=candidate.patch_text,
        changed_files=list(candidate.changed_files),
        rationale=candidate.rationale,
        status=candidate.status.value,
        hypothesis_id=candidate.hypothesis_id,
        ranking_score=candidate.ranking_score,
        residual_guidance=guidance,
        rejection_reason=candidate.rejection_reason,
    )
    if obs is not None:
        rec.selected_test_output = obs.output[-2000:]
        rec.selected_test_passed = list(obs.passed)
        rec.selected_test_failed = list(obs.failed)
        rec.selected_test_timed_out = obs.timed_out
        rec.failure_kind = obs.failure_kind
    report.candidates.append(rec)
