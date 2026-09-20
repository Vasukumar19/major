"""PatchForge Graph Reasoning & Repository Intelligence Repair Engine.

Full structural pipeline:
Issue -> Repository Indexing -> Graph-Aware Target Discovery ->
RepairContext Retrieval (CFG, StateFlow, Call Graph, Tests, Git) ->
Behavioral Diagnosis (Cause, Invariant, Strategy, Sites) ->
Grounded Patch Synthesis -> Pre-Patch Verification -> Docker SWE-bench ->
Graph-Aware Failure Refinement -> Structured RepairEpisode Recording
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
from patchforge.repair.generator import parse_edits
from patchforge.repair.patch import Patch
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
=== INVARIANT ===
=== REPAIR STRATEGY ===
=== AFFECTED SITES ===
"""

SYSTEM_PATCH_PROMPT = """You are PatchForge AI, an autonomous software-repair system.
You are given an issue, a causal diagnosis, regression invariants, and verified editable source code.
Output ONLY a single valid JSON object containing your patch action:
```json
{
  "thought": "Explanation of fix adhering to diagnosis and invariants",
  "action": {
    "name": "apply_patch",
    "arguments": {
      "patch_text": "### <file_path>\n<<<<<<< SEARCH\n<exact verbatim source code>\n=======\n<replacement code>\n>>>>>>> REPLACE"
    }
  }
}
```
Rules:
1. The SEARCH block MUST match exact verbatim lines from the VERIFIED SOURCE provided.
2. Do NOT include diff markers ('+' or '-') in SEARCH blocks.
3. The REPLACE block contains the corrected code.
4. Output ONLY the JSON object.
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


class GraphRepairEngine:
    """Coordinates repository intelligence, graph reasoning, diagnosis, and evidence-driven repair."""

    def __init__(
        self,
        provider: ModelProvider | None = None,
        model_name: str = "qwen2.5-coder:14b",
        workspace: str = ".",
    ):
        self.provider = provider or OllamaProvider(model=model_name)
        self.model_name = model_name
        self.workspace = workspace

    def _extract_patch_text(self, raw_text: str, target: RepairTarget) -> str:
        """Robustly extracts SEARCH/REPLACE patch text from LLM response."""
        if "<think>" in raw_text and "</think>" in raw_text:
            raw_text = raw_text.split("</think>")[-1].strip()
        elif "<think>" in raw_text:
            raw_text = raw_text.split("<think>")[-1].strip()

        candidates = []
        json_blocks = re.findall(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", raw_text)
        if json_blocks:
            candidates.extend(json_blocks)

        first_brace = raw_text.find("{")
        last_brace = raw_text.rfind("}")
        if first_brace != -1 and last_brace > first_brace:
            candidates.append(raw_text[first_brace:last_brace + 1])

        for c in candidates:
            try:
                data = json.loads(c)
                pt = data.get("action", {}).get("arguments", {}).get("patch_text", "")
                if pt and "<<<<<<< SEARCH" in pt:
                    return pt
            except Exception:
                pass

        # Fallback: regex search
        m = re.search(r"###\s*([^\n]+)\s*\n<<<<<<< SEARCH[\s\S]*?>>>>>>> REPLACE", raw_text)
        if m:
            return m.group(0).strip()

        return ""

    def repair(
        self,
        problem: Problem,
        repo_dir: str,
        tester: Tester | None = None,
        max_retries: int = 3,
        max_refinement_cycles: int = 3,
        max_refinements: int | None = None,
    ) -> GraphEngineResult:
        """Executes full repository-intelligence-driven repair workflow."""
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
        
        # Identify candidate file and symbol
        py_files = [
            str(p.relative_to(repo_p)).replace("\\", "/")
            for p in repo_p.glob("**/*.py")
            if p.is_file() and not any(part in ("tests", "testing", "test", "docs", ".git", "venv", ".venv", "build", "dist") for part in p.parts)
        ]
        
        # File selection with keyword overlap
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

        # Symbol selection in candidate file
        symbols = repo_map.extract_symbols(candidate_file)
        candidate_sym = symbols[0].name if symbols else ""
        best_s_score = -1.0
        for s in symbols:
            score = 0.0
            if s.name.lower() in issue_text.lower():
                score += 25.0
            if score > best_s_score:
                best_s_score = score
                candidate_sym = s.name

        source_win, win_start, win_end, behavior_context = repo_map.extract_surgical_unit(
            candidate_file, candidate_sym, issue_text
        )

        target = RepairTarget(
            repository=problem.repo,
            file_path=candidate_file,
            symbol=candidate_sym,
            line_start=win_start,
            line_end=win_end,
            source_span=(win_start, win_end),
            verified_source=source_win,
            verification_status=bool(source_win),
            behavior_context=behavior_context,
        )

        # Graph-driven secondary site discovery:
        # Check if callees or helpers in candidate file should be secondary sites
        callees = graph.callees(candidate_sym)
        for c in callees:
            if c.file_path == candidate_file and c.name != candidate_sym and c.start_line > 0:
                c_source, c_start, c_end, _ = repo_map.extract_surgical_unit(c.file_path, c.name, "")
                if c_source:
                    target.add_secondary_site(
                        EditSite(
                            file_path=c.file_path,
                            symbol=c.name,
                            line_start=c_start,
                            line_end=c_end,
                            source_span=(c_start, c_end),
                            verified_source=c_source,
                            verification_status=True,
                            site_role="RELATED_HELPER",
                            rank=2,
                        )
                    )

        result.target = target

        if not target.verification_status or not target.verified_source:
            result.failure_class = FailureClass.INFRA_FAILURE.value
            result.runtime_s = round(time.time() - t0, 2)
            result.telemetry = telemetry_logger.get_breakdown()
            _cleanup()
            return result

        # Step 3: Graph Context Retrieval
        retriever = RepairContextRetriever(repo_dir, graph, test_index)
        repair_context = retriever.retrieve(
            problem_statement=problem.problem_statement,
            target_file=target.file_path,
            target_symbol=target.symbol,
        )

        # Step 4: Behavioral Diagnosis
        diag_t0 = time.time()
        diag_prompt = BehavioralDiagnosisEngine.build_diagnostic_prompt(
            problem.problem_statement,
            repair_context,
        )
        try:
            diag_resp = self.provider.generate_one(diag_prompt, system=SYSTEM_DIAGNOSIS_PROMPT)
            diagnosis = BehavioralDiagnosisEngine.parse_diagnosis(diag_resp.text)
            telemetry_logger.log_event(
                "DIAGNOSIS", "DIAGNOSIS_COMPLETED", duration_ms=(time.time() - diag_t0) * 1000.0,
                input_tokens=diag_resp.input_tokens, output_tokens=diag_resp.output_tokens,
                details={"cause_len": len(diagnosis.cause), "strategy_len": len(diagnosis.repair_strategy)}
            )
        except Exception as e:
            logger.warning(f"Diagnosis generation failed: {e}")
            diagnosis = DiagnosisResult(
                cause="Identified divergence in target.",
                invariant="Preserve callers.",
                repair_strategy="Fix target behavior.",
                affected_sites=[f"{target.file_path}:{target.symbol}"],
                raw_response="",
            )
        result.diagnosis = diagnosis

        # Step 5: Patch Synthesis
        def build_patch_prompt(curr_target: RepairTarget, curr_diag: DiagnosisResult) -> str:
            sections = [
                "### ISSUE SPECIFICATION:",
                problem.problem_statement.strip()[:1500],
                "",
                "### CAUSAL DIAGNOSIS:",
                f"- Cause: {curr_diag.cause}",
                f"- Regression Invariant: {curr_diag.invariant}",
                f"- Repair Strategy: {curr_diag.repair_strategy}",
                "",
                "### EDITABLE SOURCE — VERBATIM:",
            ]
            for site in curr_target.all_sites():
                sections.extend([
                    f"--- File: {site.file_path} (Lines {site.line_start}-{site.line_end}, Symbol: {site.symbol}, Role: {site.site_role}) ---",
                    site.verified_source,
                    "",
                ])
            sections.extend([
                "### INSTRUCTIONS:",
                "Output ONLY a single valid JSON object with the apply_patch action.",
                "Ensure SEARCH block matches exact lines from EDITABLE SOURCE — VERBATIM above.",
            ])
            return "\n".join(sections)

        patch_prompt = build_patch_prompt(target, diagnosis)
        patch_applied = False
        applied_patch: Patch | None = None

        for attempt in range(1, max_retries + 1):
            llm_t0 = time.time()
            try:
                resp = self.provider.generate_one(patch_prompt, system=SYSTEM_PATCH_PROMPT)
                telemetry_logger.log_event(
                    "PATCH", "LLM_GENERATE", duration_ms=(time.time() - llm_t0) * 1000.0,
                    input_tokens=resp.input_tokens, output_tokens=resp.output_tokens,
                    details={"attempt": attempt}
                )
            except Exception as e:
                telemetry_logger.log_event("PATCH", "LLM_ERROR", details={"error": str(e), "attempt": attempt})
                continue

            raw_text = resp.text.strip()
            patch_text = self._extract_patch_text(raw_text, target)
            if not patch_text:
                telemetry_logger.log_event("PATCH", "PATCH_PARSE_FAILED", details={"attempt": attempt})
                continue

            apply_tool = ApplyPatchTool(repo_dir=repo_dir)
            apply_tool.write_to_disk = True
            allowed_files = [s.file_path for s in target.all_sites()]
            apply_res = apply_tool.execute({
                "patch_text": patch_text,
                "expected_target_file": target.file_path,
                "allowed_files": allowed_files,
                "write_to_disk": True,
            })

            if apply_res.status == "SUCCESS":
                patch_applied = True
                p_data = apply_res.data.get("patch", {})
                diff_proc = subprocess.run(["git", "diff"], cwd=repo_dir, capture_output=True, text=True)
                full_diff = diff_proc.stdout or apply_res.data.get("diff", "")
                applied_patch = Patch(
                    hypothesis_id="GRAPH_H1",
                    files_changed=apply_res.data.get("files_changed", [target.file_path]),
                    patch_text=full_diff,
                    valid=True,
                    match_tier=p_data.get("match_tier", "EXACT"),
                    input_tokens=resp.input_tokens,
                    output_tokens=resp.output_tokens,
                )
                telemetry_logger.log_event("APPLY", "PATCH_APPLIED", details={"diff_length": len(applied_patch.patch_text)})
                break
            else:
                telemetry_logger.log_event("APPLY", "PATCH_FAILED", details={"error": apply_res.error, "attempt": attempt})
                patch_prompt += f"\n\n[PREVIOUS ATTEMPT FAILED]: {apply_res.error}\nFix the error and produce exact verbatim SEARCH lines."

        result.patch = applied_patch
        result.patch_applied = patch_applied

        if not patch_applied or not applied_patch:
            result.failure_class = FailureClass.PATCH_SYNTAX.value
            result.runtime_s = round(time.time() - t0, 2)
            result.telemetry = telemetry_logger.get_breakdown()
            _cleanup()
            return result

        # Step 6: Test Execution & Verification
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

            # Step 7: Graph-Aware Refinement Loop
            refiner = GraphAwareFailureRefiner(repo_dir, graph, test_index)
            failure_trace = getattr(verdict, "error", "") or str(result.test_summary)

            ref_cycle = 0
            while not result.resolved and ref_cycle < max_refinement_cycles:
                ref_cycle += 1
                result.refinement_cycles = ref_cycle
                telemetry_logger.log_event("REFINEMENT", "CYCLE_START", details={"cycle": ref_cycle})

                evidence = refiner.analyze_failure(
                    primary_symbol=target.symbol,
                    primary_file=target.file_path,
                    failure_traceback=failure_trace,
                )

                # If an additional secondary site is suggested, verify and add it
                if evidence.suggested_additional_site and ":" in evidence.suggested_additional_site:
                    s_file, s_sym = evidence.suggested_additional_site.split(":", 1)
                    s_code, s_start, s_end, _ = repo_map.extract_surgical_unit(s_file, s_sym, "")
                    if s_code:
                        target.add_secondary_site(
                            EditSite(
                                file_path=s_file,
                                symbol=s_sym,
                                line_start=s_start,
                                line_end=s_end,
                                source_span=(s_start, s_end),
                                verified_source=s_code,
                                verification_status=True,
                                site_role="TRACE_DERIVED",
                                rank=3,
                            )
                        )

                # Re-synthesize with graph refinement prompt
                ref_prompt = refiner.build_refinement_prompt(
                    problem_statement=problem.problem_statement,
                    repair_context=repair_context,
                    evidence=evidence,
                    previous_patch=applied_patch.patch_text,
                )
                ref_prompt += "\n" + build_patch_prompt(target, diagnosis)

                try:
                    ref_resp = self.provider.generate_one(ref_prompt, system=SYSTEM_PATCH_PROMPT)
                    ref_patch_text = self._extract_patch_text(ref_resp.text.strip(), target)
                except Exception as e:
                    logger.warning(f"Refinement LLM call failed: {e}")
                    break

                if not ref_patch_text:
                    break

                _cleanup()
                apply_tool = ApplyPatchTool(repo_dir=repo_dir)
                apply_tool.write_to_disk = True
                allowed_files = [s.file_path for s in target.all_sites()]
                ref_apply_res = apply_tool.execute({
                    "patch_text": ref_patch_text,
                    "expected_target_file": target.file_path,
                    "allowed_files": allowed_files,
                    "write_to_disk": True,
                })

                if ref_apply_res.status == "SUCCESS":
                    diff_proc = subprocess.run(["git", "diff"], cwd=repo_dir, capture_output=True, text=True)
                    ref_full_diff = diff_proc.stdout or ref_apply_res.data.get("diff", "")
                    applied_patch = Patch(
                        hypothesis_id=f"GRAPH_REF_{ref_cycle}",
                        files_changed=ref_apply_res.data.get("files_changed", [target.file_path]),
                        patch_text=ref_full_diff,
                        valid=True,
                        match_tier="EXACT",
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
                else:
                    break

        result.runtime_s = round(time.time() - t0, 2)
        result.telemetry = telemetry_logger.get_breakdown()

        # Step 8: Save Structured Repair Episode
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
                diagnosis={
                    "cause": diagnosis.cause if diagnosis else "",
                    "invariant": diagnosis.invariant if diagnosis else "",
                    "repair_strategy": diagnosis.repair_strategy if diagnosis else "",
                    "affected_sites": diagnosis.affected_sites if diagnosis else [],
                },
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
            )
            save_repair_episode(episode)
        except Exception as e:
            logger.warning(f"Failed to record repair episode: {e}")

        _cleanup()
        return result

    run = repair
