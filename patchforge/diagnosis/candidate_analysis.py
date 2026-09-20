"""Candidate behavioral path analysis, shared-helper classification, blast-radius, and counterfactual analysis."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple

from patchforge.diagnosis.behavior import IssueBehaviorMap
from patchforge.repository_intelligence.graph import RepositoryGraph
from patchforge.repository_intelligence.schema import GraphNode
from patchforge.repository_intelligence.test_graph import TestCaseEntity, TestGraphIndex

logger = logging.getLogger(__name__)


class HelperClassification(str, Enum):
    FEATURE_LOCAL = "FEATURE_LOCAL"
    FEATURE_SHARED = "FEATURE_SHARED"
    GLOBAL_INFRASTRUCTURE = "GLOBAL_INFRASTRUCTURE"
    UNKNOWN = "UNKNOWN"


@dataclass
class CausalPath:
    """A directed behavioral execution path connecting tests/callers to candidate and callees."""
    candidate_symbol: str
    file_path: str
    path_nodes: List[str] = field(default_factory=list)
    role_in_path: str = "INTERMEDIATE"  # ENTRY_POINT, INTERMEDIATE_DISPATCHER, STATE_MUTATOR, TERMINAL_HELPER
    state_mutations: List[str] = field(default_factory=list)
    path_str: str = ""

    def __post_init__(self):
        if not self.path_str and self.path_nodes:
            self.path_str = " -> ".join(self.path_nodes)


@dataclass
class CandidateBlastRadius:
    """Structural blast-radius of modifying this candidate."""
    direct_callers_count: int = 0
    transitive_callers_count: int = 0
    direct_callees_count: int = 0
    cross_module_callers_count: int = 0
    affected_modules_count: int = 1
    affected_tests_count: int = 0
    semantic_radius_score: float = 1.0  # Normalized 1.0 (local/small) to 10.0 (global infrastructure)
    classification: HelperClassification = HelperClassification.UNKNOWN

    def format_summary(self) -> str:
        return (
            f"Blast Radius [{self.classification.value}]: "
            f"{self.direct_callers_count} callers ({self.cross_module_callers_count} cross-module), "
            f"{self.affected_tests_count} tests, radius score: {self.semantic_radius_score:.1f}"
        )


@dataclass
class CounterfactualEvaluation:
    """Evaluates whether modifying this candidate explains all observed symptoms without breaking invariants."""
    candidate_symbol: str
    explains_failure: bool = False
    explains_expected_behavior: bool = False
    explains_scope: bool = False
    unexplained_symptoms: List[str] = field(default_factory=list)
    verdict: str = "PLAUSIBLE"  # HIGHLY_PLAUSIBLE, PLAUSIBLE, INSUFFICIENT_SCOPE, WEAK, ELIMINATED
    reasons: List[str] = field(default_factory=list)

    def format_summary(self) -> str:
        symptom_info = f" ({len(self.unexplained_symptoms)} unexplained symptoms)" if self.unexplained_symptoms else " (all symptoms covered)"
        return f"Counterfactual [{self.verdict}]: failure={self.explains_failure}, expected={self.explains_expected_behavior}, scope={self.explains_scope}{symptom_info}"


@dataclass
class CandidateProfile:
    """Full behavioral profile for a candidate repair site."""
    symbol: str
    file_path: str
    start_line: int
    end_line: int
    ast_node_type: str = "FunctionDef"
    causal_paths: List[CausalPath] = field(default_factory=list)
    blast_radius: CandidateBlastRadius = field(default_factory=CandidateBlastRadius)
    counterfactual: CounterfactualEvaluation = field(default_factory=lambda: CounterfactualEvaluation(candidate_symbol=""))
    direct_tests: List[str] = field(default_factory=list)
    distinguishing_tests: List[str] = field(default_factory=list)
    evidence_score: float = 100.0
    evidence_reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "file_path": self.file_path,
            "span": f"L{self.start_line}-L{self.end_line}",
            "classification": self.blast_radius.classification.value,
            "blast_radius": self.blast_radius.format_summary(),
            "counterfactual": self.counterfactual.format_summary(),
            "evidence_score": self.evidence_score,
            "reasons": self.evidence_reasons,
        }


class CandidateBehaviorAnalyzer:
    """Analyzes candidates with repository graph, causal paths, blast-radius, and counterfactuals."""

    def __init__(
        self,
        graph: Optional[RepositoryGraph] = None,
        test_index: Optional[TestGraphIndex] = None,
    ):
        self.graph = graph
        self.test_index = test_index

    def analyze_candidate(
        self,
        symbol: str,
        file_path: str,
        start_line: int,
        end_line: int,
        behavior_map: Optional[IssueBehaviorMap] = None,
        failing_traceback: Optional[str] = None,
    ) -> CandidateProfile:
        """Constructs a comprehensive behavioral profile for a candidate."""
        clean_sym = symbol.split(".")[-1]
        clean_file = file_path.replace("\\", "/")

        # 1. Compute Blast Radius and Classification
        blast = self._compute_blast_radius(clean_sym, clean_file, start_line, end_line)

        # 2. Extract Causal Paths
        paths = self._build_causal_paths(clean_sym, clean_file, behavior_map)

        # 3. Associate Tests
        direct_tests = []
        if self.test_index:
            for t in self.test_index.tests_for_symbol(clean_sym):
                direct_tests.append(t.name)

        # 4. Counterfactual Evaluation
        counterfactual = self._evaluate_counterfactual(
            symbol=clean_sym,
            file_path=clean_file,
            blast=blast,
            paths=paths,
            behavior_map=behavior_map,
            failing_traceback=failing_traceback,
        )

        return CandidateProfile(
            symbol=symbol,
            file_path=file_path,
            start_line=start_line,
            end_line=end_line,
            causal_paths=paths,
            blast_radius=blast,
            counterfactual=counterfactual,
            direct_tests=direct_tests,
        )

    def _compute_blast_radius(
        self, symbol: str, file_path: str, start_line: int, end_line: int
    ) -> CandidateBlastRadius:
        direct_callers: List[GraphNode] = []
        transitive_callers: Set[str] = set()
        direct_callees: List[GraphNode] = []
        cross_module = 0
        affected_modules: Set[str] = set()

        curr_mod = file_path.rsplit("/", 1)[0] if "/" in file_path else ""
        if curr_mod:
            affected_modules.add(curr_mod)

        if self.graph:
            callers = self.graph.callers(symbol)
            direct_callers = callers
            for c in callers:
                c_file = getattr(c, "file_path", "").replace("\\", "/")
                c_mod = c_file.rsplit("/", 1)[0] if "/" in c_file else ""
                if c_mod:
                    affected_modules.add(c_mod)
                if c_mod and c_mod != curr_mod:
                    cross_module += 1
                transitive_callers.add(getattr(c, "name", str(c)))
                # 2-hop transitive
                if hasattr(self.graph, "callers"):
                    for c2 in self.graph.callers(getattr(c, "name", str(c))):
                        transitive_callers.add(getattr(c2, "name", str(c2)))

            direct_callees = self.graph.callees(symbol)

        d_callers_cnt = len(direct_callers)
        t_callers_cnt = len(transitive_callers)
        d_callees_cnt = len(direct_callees)

        # Tests affected
        tests_cnt = 0
        if self.test_index:
            tests_cnt = len(self.test_index.tests_for_symbol(symbol))

        # Classification (Section 6)
        if cross_module > 3 or d_callers_cnt > 12 or len(affected_modules) > 4:
            classification = HelperClassification.GLOBAL_INFRASTRUCTURE
            score_base = 7.0 + min(3.0, cross_module * 0.5)
        elif d_callers_cnt > 3 or (d_callers_cnt > 1 and cross_module >= 1):
            classification = HelperClassification.FEATURE_SHARED
            score_base = 4.0 + min(2.5, d_callers_cnt * 0.3)
        elif d_callers_cnt <= 2 and cross_module == 0:
            classification = HelperClassification.FEATURE_LOCAL
            score_base = 1.0 + min(2.0, d_callers_cnt * 0.5)
        else:
            classification = HelperClassification.UNKNOWN
            score_base = 3.0

        span = max(1, end_line - start_line + 1)
        radius_score = min(10.0, score_base + (0.5 if span > 100 else 0.0))

        return CandidateBlastRadius(
            direct_callers_count=d_callers_cnt,
            transitive_callers_count=t_callers_cnt,
            direct_callees_count=d_callees_cnt,
            cross_module_callers_count=cross_module,
            affected_modules_count=len(affected_modules),
            affected_tests_count=tests_cnt,
            semantic_radius_score=round(radius_score, 1),
            classification=classification,
        )

    def _build_causal_paths(
        self, symbol: str, file_path: str, behavior_map: Optional[IssueBehaviorMap]
    ) -> List[CausalPath]:
        paths: List[CausalPath] = []
        if not self.graph:
            return paths

        callers = self.graph.callers(symbol)
        callees = self.graph.callees(symbol)

        caller_names = [getattr(c, "name", str(c)) for c in callers[:3]]
        callee_names = [getattr(c, "name", str(c)) for c in callees[:3]]

        # Try to find a test or entry point caller
        entry_tests = []
        if behavior_map and behavior_map.mentioned_tests:
            entry_tests = behavior_map.mentioned_tests[:2]

        if entry_tests and caller_names:
            for t in entry_tests:
                nodes = [t, caller_names[0], symbol] + (callee_names[:1] if callee_names else [])
                paths.append(CausalPath(candidate_symbol=symbol, file_path=file_path, path_nodes=nodes, role_in_path="INTERMEDIATE_DISPATCHER"))
        elif caller_names:
            nodes = [caller_names[0], symbol] + (callee_names[:2] if callee_names else [])
            paths.append(CausalPath(candidate_symbol=symbol, file_path=file_path, path_nodes=nodes, role_in_path="INTERMEDIATE"))
        elif callee_names:
            nodes = [symbol] + callee_names
            paths.append(CausalPath(candidate_symbol=symbol, file_path=file_path, path_nodes=nodes, role_in_path="ENTRY_POINT"))
        else:
            paths.append(CausalPath(candidate_symbol=symbol, file_path=file_path, path_nodes=[symbol], role_in_path="LEAF_FUNCTION"))

        return paths

    def _evaluate_counterfactual(
        self,
        symbol: str,
        file_path: str,
        blast: CandidateBlastRadius,
        paths: List[CausalPath],
        behavior_map: Optional[IssueBehaviorMap],
        failing_traceback: Optional[str],
    ) -> CounterfactualEvaluation:
        reasons: List[str] = []
        unexplained: List[str] = []

        explains_failure = False
        explains_expected = False
        explains_scope = False

        sym_lower = symbol.lower()
        file_lower = file_path.lower()
        tb_lower = (failing_traceback or "").lower()

        # Check failure explanation via traceback or trigger keywords
        if tb_lower:
            if sym_lower in tb_lower:
                explains_failure = True
                reasons.append(f"Symbol '{symbol}' is directly present on execution failure stack.")
            elif file_lower.split("/")[-1] in tb_lower:
                explains_failure = True
                reasons.append(f"Candidate file '{file_path}' is on failure stack.")
        else:
            explains_failure = True

        # Check symptoms coverage (Section 8 & 9)
        if behavior_map and behavior_map.symptoms:
            for s in behavior_map.symptoms:
                s_lower = s.lower()
                # Check if this symptom relates to this symbol or file or its callers
                matches = sym_lower in s_lower or any(p.lower() in s_lower for path in paths for p in path.path_nodes)
                if not matches:
                    unexplained.append(s[:60])
            if not unexplained:
                reasons.append("Covers all observed symptoms in issue.")
            else:
                reasons.append(f"Does not directly cover {len(unexplained)} symptom(s).")

        # Check expected behavior explanation
        if behavior_map and behavior_map.expected_behavior:
            exp_lower = behavior_map.expected_behavior.lower()
            if sym_lower in exp_lower or any(kw in exp_lower for kw in sym_lower.split("_")):
                explains_expected = True
                reasons.append("Symbol semantics directly align with expected behavior description.")
            else:
                explains_expected = True  # Plausible scope
        else:
            explains_expected = True

        # Check scope (Section 6: call-site vs global helper)
        if blast.classification == HelperClassification.GLOBAL_INFRASTRUCTURE:
            explains_scope = False
            reasons.append(f"Candidate is GLOBAL_INFRASTRUCTURE with {blast.cross_module_callers_count} cross-module callers; high risk of collateral regression.")
        else:
            explains_scope = True
            reasons.append(f"Candidate has targeted {blast.classification.value} scope.")

        # Overall verdict
        if explains_failure and explains_expected and explains_scope and len(unexplained) <= 1:
            verdict = "HIGHLY_PLAUSIBLE"
        elif explains_failure and explains_scope:
            verdict = "PLAUSIBLE"
        elif not explains_scope:
            verdict = "INSUFFICIENT_SCOPE"
        else:
            verdict = "WEAK"

        return CounterfactualEvaluation(
            candidate_symbol=symbol,
            explains_failure=explains_failure,
            explains_expected_behavior=explains_expected,
            explains_scope=explains_scope,
            unexplained_symptoms=unexplained,
            verdict=verdict,
            reasons=reasons,
        )
