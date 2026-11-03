"""Evidence-driven repair site ranker for PatchForge AI.

Evaluates candidate edit sites from graph intelligence, tracebacks, and diagnosis.
Prefers the smallest repair site capable of satisfying the diagnosed invariant
while penalizing shared helpers with high regression blast radius.
NO task-specific rules; purely evidence-driven.
"""
from __future__ import annotations

import logging
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Set

from patchforge.core.target import EditSite
from patchforge.issue.problem import Problem
from patchforge.repair.schema import RankedRepairSite
from patchforge.repository_intelligence.diagnosis import DiagnosisResult
from patchforge.repository_intelligence.graph import RepositoryGraph
from patchforge.repository_intelligence.schema import GraphNode

logger = logging.getLogger(__name__)


def _paths_match(file_a: str, file_b: str) -> bool:
    """Robust path matching supporting relative paths and suffix matches without cross-directory collisions."""
    a = file_a.replace("\\", "/").strip("` \t\r\n").lstrip("/").lower()
    b = file_b.replace("\\", "/").strip("` \t\r\n").lstrip("/").lower()
    if not a or not b:
        return False
    if a == b:
        return True
    if a.endswith("/" + b) or b.endswith("/" + a):
        return True
    if "/" in a and "/" in b:
        return False
    base_a = a.split("/")[-1]
    base_b = b.split("/")[-1]
    return base_a == base_b


class RepairSiteRanker:
    """Ranks candidate repair sites based on repository structural evidence and risk analysis."""

    def __init__(self, graph: Optional[RepositoryGraph] = None):
        self.graph = graph

    def rank_sites(
        self,
        candidates: List[EditSite | GraphNode | dict[str, Any]],
        problem: Optional[Problem] = None,
        diagnosis: Optional[DiagnosisResult | Any] = None,
        test_traceback: Optional[str] = None,
        primary_file: str = "",
        behavior_map: Optional[Any] = None,
        candidate_profiles: Optional[Dict[str, Any]] = None,
    ) -> List[RankedRepairSite]:
        """Ranks candidate repair sites and returns sorted list of RankedRepairSite."""
        ranked: List[RankedRepairSite] = []
        if not candidates:
            return ranked

        # Normalize issue text terms for keyword relevance
        issue_text = ""
        if problem:
            issue_text = f"{getattr(problem, 'problem_statement', '')} {getattr(problem, 'hints_text', '')}".lower()

        traceback_text = (test_traceback or "").lower()

        diag_sites_text = ""
        if diagnosis:
            diag_sites_text = " ".join(diagnosis.affected_sites).lower() + " " + diagnosis.cause.lower()

        seen_keys: Set[str] = set()

        # Parse structured traceback frames if present
        tb_frames: List[Tuple[str, int, str]] = []
        if traceback_text:
            raw_frames = re.findall(
                r'File\s+"([^"]+)",\s+line\s+(\d+)(?:,\s+in\s+([a-zA-Z0-9_]+))?',
                traceback_text,
                re.IGNORECASE,
            )
            for f, l, s in raw_frames:
                try:
                    tb_frames.append((f.replace("\\", "/").lower(), int(l), (s or "").lower()))
                except ValueError:
                    pass

        # Identify innermost repository stack frame across candidates
        candidate_files = {
            (getattr(c, "file_path", None) or (c.get("file_path") if isinstance(c, dict) else "")).replace("\\", "/").split("/")[-1].lower()
            for c in candidates
        } - {""}
        deepest_repo_idx = -1
        if tb_frames:
            for idx, (f_path, f_line, f_sym) in enumerate(tb_frames):
                p_parts = Path(f_path).parts
                if any(p in p_parts for p in ("packages", "vendor", "third_party")):
                    continue
                file_name = p_parts[-1] if p_parts else ""
                if file_name.startswith("test_") or file_name.endswith("_test.py"):
                    continue
                if candidate_files and file_name in candidate_files:
                    deepest_repo_idx = idx

        for cand in candidates:
            # Extract basic attributes depending on candidate type
            if isinstance(cand, EditSite):
                file_path = cand.file_path
                symbol = cand.symbol
                line_start = cand.line_start
                line_end = cand.line_end
                role = cand.site_role
                ast_node_type = getattr(cand, "node_type", "FunctionDef")
            elif isinstance(cand, GraphNode):
                file_path = cand.file_path
                symbol = cand.name
                line_start = cand.start_line
                line_end = cand.end_line
                role = "PRIMARY_FUNCTION"
                ast_node_type = cand.kind.value if hasattr(cand.kind, "value") else str(cand.kind)
            elif isinstance(cand, dict):
                file_path = cand.get("file_path", "")
                symbol = cand.get("symbol", cand.get("name", ""))
                line_start = cand.get("line_start", cand.get("start_line", 1))
                line_end = cand.get("line_end", cand.get("end_line", 1))
                role = cand.get("site_role", cand.get("role", "PRIMARY"))
                ast_node_type = cand.get("node_type", "FunctionDef")
            else:
                continue

            dedup_key = f"{file_path}::{symbol}::{line_start}"
            if dedup_key in seen_keys:
                continue
            seen_keys.add(dedup_key)

            score = 100.0
            reasons: List[str] = []

            # 1. Traceback Evidence (+60 pts)
            in_traceback = False
            clean_sym = symbol.split(".")[-1].lower() if symbol else ""
            clean_file = file_path.replace("\\", "/").split("/")[-1].lower() if file_path else ""

            if clean_sym and clean_sym in traceback_text:
                score += 50.0
                in_traceback = True
                reasons.append(f"Symbol '{symbol}' appears directly in failing test traceback (+50)")
            if clean_file and clean_file in traceback_text:
                score += 20.0
                in_traceback = True
                reasons.append(f"File '{clean_file}' appears in failing test traceback (+20)")

            # Frame line coverage & depth bonus (prefer innermost frames closer to failure)
            if tb_frames and clean_file:
                matching_depth = -1
                matched_line = -1
                total_frames = len(tb_frames)
                for idx, (f_path, f_line, f_sym) in enumerate(tb_frames):
                    if clean_file in f_path:
                        if line_start <= f_line <= line_end:
                            matching_depth = idx
                            matched_line = f_line
                if matching_depth != -1:
                    score += 30.0
                    depth_ratio = (matching_depth + 1) / total_frames
                    depth_bonus = round(depth_ratio * 30.0, 1)
                    score += depth_bonus
                    in_traceback = True
                    reasons.append(
                        f"Covers traceback line {matched_line} at stack frame depth {matching_depth + 1}/{total_frames} (+{30 + depth_bonus:.1f})"
                    )
                    if deepest_repo_idx != -1 and matching_depth == deepest_repo_idx:
                        score += 30.0
                        reasons.append(
                            f"Innermost repository stack frame line {matched_line} (depth {matching_depth + 1}/{total_frames}) (+30)"
                        )

            # 2. Diagnosis Evidence & Coherent Target Alignment
            is_direct_diagnosis_site = False
            is_structurally_related = False
            has_affected_sites = bool(diagnosis and getattr(diagnosis, "affected_sites", None))

            if has_affected_sites:
                for aff in diagnosis.affected_sites:
                    if ":" in aff:
                        af_f, af_s = aff.split(":", 1)
                        af_s_clean = af_s.strip("` \t\r\n()[]").lower()
                        if _paths_match(file_path, af_f) and (clean_sym == af_s_clean or clean_sym == af_s_clean.split(".")[-1]):
                            is_direct_diagnosis_site = True
                            break
                    elif clean_sym and clean_sym == aff.strip("` \t\r\n()[]").lower():
                        is_direct_diagnosis_site = True
                        break

                if is_direct_diagnosis_site:
                    score += 80.0
                    reasons.append(f"Direct match with diagnosed root-cause site '{symbol}' (+80)")
                elif self.graph and symbol:
                    for aff in diagnosis.affected_sites:
                        aff_sym = aff.split(":", 1)[1] if ":" in aff else aff
                        try:
                            if symbol in self.graph.callers(aff_sym):
                                is_structurally_related = True
                                score += 30.0
                                reasons.append(f"Candidate is a direct caller of diagnosed site '{aff_sym}' (+30)")
                                break
                            elif symbol in self.graph.callees(aff_sym):
                                is_structurally_related = True
                                score += 25.0
                                reasons.append(f"Candidate is a callee/helper of diagnosed site '{aff_sym}' (+25)")
                                break
                        except Exception:
                            pass

                if not is_direct_diagnosis_site and not is_structurally_related:
                    if clean_sym and clean_sym in diag_sites_text:
                        score += 30.0
                        reasons.append(f"Symbol '{symbol}' mentioned in diagnosis rationale (+30)")
                    elif not in_traceback:
                        # Candidate is neither diagnosed, nor structurally connected, nor in traceback
                        score -= 80.0
                        reasons.append("Unexplained divergence from diagnosed defect location (-80)")
            elif diagnosis:
                if clean_sym and clean_sym in diag_sites_text:
                    score += 40.0
                    reasons.append(f"Symbol '{symbol}' identified in behavioral diagnosis (+40)")
                elif clean_file and clean_file in diag_sites_text:
                    score += 20.0
                    reasons.append(f"File '{clean_file}' identified in behavioral diagnosis (+20)")

            # 3. Issue Relevance (+25 pts)
            if issue_text and clean_sym:
                # Token matching
                sym_tokens = re.findall(r"[a-z0-9]+", clean_sym)
                matching_tokens = [t for t in sym_tokens if len(t) > 2 and t in issue_text]
                if matching_tokens:
                    boost = min(30.0, 10.0 * len(matching_tokens))
                    score += boost
                    reasons.append(f"Symbol matches issue keywords {matching_tokens} (+{boost:.0f})")

            # 4. Shared Helper Risk & Unrelated Callers Analysis
            unrelated_callers_count = 0
            if self.graph and symbol:
                callers = self.graph.callers(symbol)
                # Count callers outside the same module
                curr_mod = file_path.replace("\\", "/").rsplit("/", 1)[0]
                for c in callers:
                    c_node = c if isinstance(c, GraphNode) else (self.graph.nodes.get(c) if hasattr(self.graph, "nodes") else None)
                    if c_node and hasattr(c_node, "file_path") and not c_node.file_path.replace("\\", "/").startswith(curr_mod):
                        unrelated_callers_count += 1

                if unrelated_callers_count > 3:
                    penalty = min(40.0, unrelated_callers_count * 4.0)
                    score -= penalty
                    reasons.append(
                        f"High regression risk: symbol has {unrelated_callers_count} cross-module callers (-{penalty:.0f})"
                    )

            # 5. Call-Site vs Helper Preference
            # If the candidate is a call-site caller, boost it over a deep shared helper
            if role in ("CALL_SITE", "RELATED_CALLER") or "call_site" in role.lower():
                score += 25.0
                reasons.append("Call-site proximity preferred over global helper modification (+25)")
            elif role in ("RELATED_HELPER", "SHARED_UTILITY") and unrelated_callers_count > 1:
                score -= 15.0
                reasons.append("Shared helper has wider blast radius than calling site (-15)")

            # 6. Primary File Proximity
            if primary_file and file_path == primary_file:
                score += 15.0
                reasons.append("Located in primary localized file (+15)")

            # 7. Semantic Radius Penalty (prefer smaller surgical blocks)
            span_lines = max(1, line_end - line_start + 1)
            if span_lines > 200:
                score -= 20.0
                reasons.append(f"Large code span ({span_lines} lines) carries higher blast radius (-20)")
            elif span_lines <= 30:
                score += 10.0
                reasons.append(f"Compact code span ({span_lines} lines) preferred (+10)")

            # 8. Behavioral Evidence & Counterfactual Integration (v0.7)
            if candidate_profiles:
                norm_f = file_path.replace("\\", "/") if file_path else ""
                base_f = norm_f.split("/")[-1] if norm_f else ""
                c_prof = None
                if norm_f:
                    c_prof = candidate_profiles.get(f"{norm_f}:{symbol}") or candidate_profiles.get(f"{base_f}:{symbol}")
                if not c_prof and norm_f:
                    c_prof = candidate_profiles.get(f"{norm_f}:{clean_sym}") or candidate_profiles.get(f"{base_f}:{clean_sym}")
                if not c_prof:
                    c_prof = candidate_profiles.get(symbol) or candidate_profiles.get(clean_sym)
                if c_prof:
                    cf = getattr(c_prof, "counterfactual", None)
                    if cf:
                        if getattr(cf, "verdict", "") == "HIGHLY_PLAUSIBLE":
                            score += 30.0
                            reasons.append("Counterfactual: highly plausible causal coverage (+30)")
                        elif getattr(cf, "verdict", "") == "PLAUSIBLE":
                            score += 15.0
                            reasons.append("Counterfactual: plausible coverage (+15)")
                        elif getattr(cf, "verdict", "") in ("INSUFFICIENT_SCOPE", "WEAK"):
                            score -= 25.0
                            reasons.append(f"Counterfactual: {getattr(cf, 'verdict', '')} causal link (-25)")

                        unexp = getattr(cf, "unexplained_symptoms", [])
                        if unexp:
                            p = min(20.0, len(unexp) * 10.0)
                            score -= p
                            reasons.append(f"Fails to cover {len(unexp)} issue symptoms (-{p:.0f})")
                        else:
                            score += 15.0
                            reasons.append("Covers all observed issue symptoms (+15)")

                    br = getattr(c_prof, "blast_radius", None)
                    if br and hasattr(br, "classification"):
                        c_val = br.classification.value if hasattr(br.classification, "value") else str(br.classification)
                        if c_val == "FEATURE_LOCAL":
                            score += 25.0
                            reasons.append("Blast radius: FEATURE_LOCAL with bounded impact (+25)")
                        elif c_val == "FEATURE_SHARED":
                            score += 10.0
                            reasons.append("Blast radius: FEATURE_SHARED within feature (+10)")
                        elif c_val == "GLOBAL_INFRASTRUCTURE":
                            score -= 30.0
                            reasons.append("Blast radius: GLOBAL_INFRASTRUCTURE carrying high regression risk (-30)")

                    d_tests = getattr(c_prof, "direct_tests", [])
                    if d_tests:
                        score += 15.0
                        reasons.append(f"Direct tests available: {len(d_tests)} test(s) (+15)")

            # Vendored / third-party code penalty (-100)
            if any(part in ("packages", "vendor", "third_party") for part in Path(file_path).parts):
                score -= 100.0
                reasons.append("Vendored / third-party code discouraged (-100)")

            ranked.append(
                RankedRepairSite(
                    file_path=file_path,
                    symbol=symbol,
                    role=role,
                    score=round(score, 2),
                    reasons=reasons,
                    line_start=line_start,
                    line_end=line_end,
                    unrelated_callers_count=unrelated_callers_count,
                    in_failing_traceback=in_traceback,
                    semantic_radius=span_lines,
                    ast_node_type=ast_node_type,
                )
            )

        # Sort descending by score
        ranked.sort(key=lambda s: s.score, reverse=True)
        return ranked

    @classmethod
    def reconcile_target(
        cls,
        candidate_site: Any,
        diagnosis: Optional[Any] = None,
        graph: Optional[RepositoryGraph] = None,
    ) -> Tuple[bool, str]:
        """Validates coherence between candidate edit site and diagnosed root cause."""
        if not diagnosis or not getattr(diagnosis, "affected_sites", None):
            return True, "No specific affected sites declared in diagnosis."

        cand_sym = getattr(candidate_site, "symbol", "") or ""
        cand_file = getattr(candidate_site, "file_path", "") or ""
        clean_sym = cand_sym.split(".")[-1].lower()

        # 1. Exact match with diagnosed affected site
        for aff in diagnosis.affected_sites:
            if ":" in aff:
                af_f, af_s = aff.split(":", 1)
                af_s_clean = af_s.strip("` \t\r\n()[]").lower()
                if _paths_match(cand_file, af_f) and (clean_sym == af_s_clean or clean_sym == af_s_clean.split(".")[-1]):
                    return True, f"Target matches hypothesized affected site '{aff}'."
            elif clean_sym and clean_sym == aff.strip("` \t\r\n()[]").lower():
                return True, f"Target symbol matches hypothesized affected site '{aff}'."

        # 2. Structural caller / callee relationship
        if graph and cand_sym:
            for aff in diagnosis.affected_sites:
                aff_sym = aff.split(":", 1)[1] if ":" in aff else aff
                try:
                    if cand_sym in graph.callers(aff_sym):
                        return True, f"Target '{cand_sym}' is a direct caller of diagnosed site '{aff_sym}'."
                    if cand_sym in graph.callees(aff_sym):
                        return True, f"Target '{cand_sym}' is a callee/helper of diagnosed site '{aff_sym}'."
                except Exception:
                    pass

        # 3. Same file and symbol mentioned in diagnosis cause / rationale
        diag_text = (getattr(diagnosis, "cause", "") + " " + getattr(diagnosis, "repair_strategy", "")).lower()
        for aff in diagnosis.affected_sites:
            if ":" in aff:
                af_f = aff.split(":", 1)[0]
                if _paths_match(cand_file, af_f) and clean_sym in diag_text:
                    return True, f"Target '{cand_sym}' is in diagnosed file '{af_f}' and supported by diagnosis rationale."

        return False, f"Target '{cand_sym}' in '{cand_file}' contradicts diagnosed affected sites {diagnosis.affected_sites} without structural justification."
