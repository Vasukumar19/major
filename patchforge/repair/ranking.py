"""Evidence-driven repair site ranker for PatchForge AI.

Evaluates candidate edit sites from graph intelligence, tracebacks, and diagnosis.
Prefers the smallest repair site capable of satisfying the diagnosed invariant
while penalizing shared helpers with high regression blast radius.
NO task-specific rules; purely evidence-driven.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Set

from patchforge.core.target import EditSite
from patchforge.issue.problem import Problem
from patchforge.repair.schema import RankedRepairSite
from patchforge.repository_intelligence.diagnosis import DiagnosisResult
from patchforge.repository_intelligence.graph import RepositoryGraph
from patchforge.repository_intelligence.schema import GraphNode

logger = logging.getLogger(__name__)


class RepairSiteRanker:
    """Ranks candidate repair sites based on repository structural evidence and risk analysis."""

    def __init__(self, graph: Optional[RepositoryGraph] = None):
        self.graph = graph

    def rank_sites(
        self,
        candidates: List[EditSite | GraphNode | dict[str, Any]],
        problem: Optional[Problem] = None,
        diagnosis: Optional[DiagnosisResult] = None,
        test_traceback: Optional[str] = None,
        primary_file: str = "",
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

            # 2. Diagnosis Evidence (+40 pts)
            if diagnosis:
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
