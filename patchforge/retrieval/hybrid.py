"""Hybrid Repository Retrieval: fuses lexical, dense, code graph, test graph, and temporal signals."""
from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from patchforge.repository_intelligence.graph import RepositoryGraph
from patchforge.repository_intelligence.schema import EdgeKind, NodeKind
from patchforge.repository_intelligence.temporal_graph import TemporalRepositoryGraph
from patchforge.repository_intelligence.test_graph import TestGraphIndex


@dataclass
class RetrievedCandidate:
    """A code candidate retrieved and scored through hybrid multi-channel fusion."""
    file_path: str
    symbol: str
    kind: str = "FUNCTION"
    line_start: int = 1
    line_end: int = 1
    score: float = 0.0
    scores_by_channel: Dict[str, float] = field(default_factory=dict)
    provenance: List[str] = field(default_factory=list)
    docstring: str = ""
    source_snippet: str = ""

    def format_summary(self) -> str:
        lines = [f"Candidate `{self.file_path}:{self.symbol}` (Score: {self.score:.3f}):"]
        lines.append("  Channels: " + ", ".join(f"{k}={v:.2f}" for k, v in self.scores_by_channel.items()))
        if self.provenance:
            lines.append("  Provenance: " + "; ".join(self.provenance[:3]))
        return "\n".join(lines)


class HybridRepositoryRetriever:
    """Fuses multi-channel signals (Lexical, Dense, Graph, Test, Temporal) into a ranked candidate list."""

    def __init__(
        self,
        repo_dir: str,
        code_graph: Optional[RepositoryGraph] = None,
        test_index: Optional[TestGraphIndex] = None,
        temporal_graph: Optional[TemporalRepositoryGraph] = None,
        weights: Optional[Dict[str, float]] = None,
    ):
        self.repo_dir = Path(repo_dir)
        self.code_graph = code_graph
        self.test_index = test_index
        self.temporal_graph = temporal_graph
        self.weights = weights or {
            "lexical": 0.25,
            "dense": 0.15,
            "graph": 0.25,
            "traceback": 0.20,
            "temporal": 0.15,
        }

    def _tokenize(self, text: str) -> List[str]:
        return [w.lower() for w in re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", text) if len(w) > 2]

    def _compute_bm25_score(self, query_tokens: List[str], doc_tokens: List[str], avg_len: float = 100.0) -> float:
        if not query_tokens or not doc_tokens:
            return 0.0
        score = 0.0
        doc_len = len(doc_tokens)
        k1 = 1.5
        b = 0.75
        for q in set(query_tokens):
            f = doc_tokens.count(q)
            if f > 0:
                tf = (f * (k1 + 1.0)) / (f + k1 * (1.0 - b + b * (doc_len / avg_len)))
                score += tf
        return min(1.0, score / (len(query_tokens) * 1.5 + 1e-5))

    def _compute_dense_score(self, query_tokens: List[str], doc_tokens: List[str]) -> float:
        """Character 3-gram vector cosine similarity across token vocabulary."""
        if not query_tokens or not doc_tokens:
            return 0.0

        def _get_ngrams(tokens: List[str]) -> Dict[str, int]:
            ng: Dict[str, int] = {}
            for t in tokens:
                t_padded = f"^{t}$"
                for i in range(max(1, len(t_padded) - 2)):
                    gram = t_padded[i : i + 3]
                    ng[gram] = ng.get(gram, 0) + 1
            return ng

        q_vec = _get_ngrams(query_tokens)
        d_vec = _get_ngrams(doc_tokens)
        if not q_vec or not d_vec:
            return 0.0

        dot = sum(v * d_vec.get(k, 0) for k, v in q_vec.items())
        norm_q = math.sqrt(sum(v * v for v in q_vec.values()))
        norm_d = math.sqrt(sum(v * v for v in d_vec.values()))
        if norm_q <= 0.0 or norm_d <= 0.0:
            return 0.0
        return min(1.0, dot / (norm_q * norm_d))

    def retrieve(
        self,
        query: str,
        traceback_text: Optional[str] = None,
        top_k: int = 10,
        filter_file: Optional[str] = None,
    ) -> List[RetrievedCandidate]:
        """Performs multi-channel scoring and returns normalized, fused candidates."""
        query_tokens = self._tokenize(query)
        tb_tokens = self._tokenize(traceback_text or "")
        candidates_map: Dict[str, RetrievedCandidate] = {}

        # 1. Collect candidate symbols from code graph
        if self.code_graph:
            for nid, node in self.code_graph.nodes.items():
                if node.kind not in (NodeKind.FUNCTION, NodeKind.METHOD, NodeKind.CLASS):
                    continue
                if filter_file and node.file_path != filter_file:
                    continue
                # Exclude test and documentation files from repair candidates
                if any(part in ("tests", "testing", "test", "docs", ".git", "venv", ".venv", "build", "dist") for part in Path(node.file_path).parts):
                    continue

                cand_key = f"{node.file_path}:{node.name}"
                doc_text = f"{node.name} {node.docstring or ''}"
                doc_tokens = self._tokenize(doc_text)

                # Channel A: Lexical
                lex_score = self._compute_bm25_score(query_tokens, doc_tokens)

                # Channel B: Dense / Semantic
                dense_score = self._compute_dense_score(query_tokens, doc_tokens)

                # Channel C: Graph structure
                graph_score = 0.0
                prov = []
                callers_count = len(node.callers) if hasattr(node, "callers") else 0
                callees_count = len(node.callees) if hasattr(node, "callees") else 0
                if callers_count > 0 or callees_count > 0:
                    graph_score += min(1.0, (callers_count + callees_count) / 10.0)
                    prov.append(f"connected to {callers_count} callers, {callees_count} callees")

                # Channel D: Traceback relevance
                tb_score = 0.0
                if tb_tokens:
                    sym_parts = [node.name.lower(), node.name.split(".")[-1].lower()]
                    if any(sp in tb_tokens for sp in sym_parts):
                        tb_score = 1.0
                        prov.append("name matched directly in failing traceback")
                    elif Path(node.file_path).name.lower() in tb_tokens:
                        tb_score = 0.6
                        prov.append("file matched in failing traceback")

                # Channel E: Temporal Graph relevance
                temp_score = 0.0
                if self.temporal_graph:
                    t_prof = self.temporal_graph.get_symbol_profile(node.file_path, node.name)
                    temp_score = t_prof.churn_score
                    if t_prof.bug_fix_commits > 0:
                        prov.append(f"historically involved in {t_prof.bug_fix_commits} bug fixes")

                # Fuse Scores
                total_score = (
                    self.weights.get("lexical", 0.25) * lex_score
                    + self.weights.get("dense", 0.15) * dense_score
                    + self.weights.get("graph", 0.25) * graph_score
                    + self.weights.get("traceback", 0.20) * tb_score
                    + self.weights.get("temporal", 0.15) * temp_score
                )

                candidates_map[cand_key] = RetrievedCandidate(
                    file_path=node.file_path,
                    symbol=node.name,
                    kind=node.kind.value if hasattr(node.kind, "value") else str(node.kind),
                    line_start=node.start_line,
                    line_end=node.end_line,
                    score=round(total_score, 4),
                    scores_by_channel={
                        "lexical": round(lex_score, 3),
                        "dense": round(dense_score, 3),
                        "graph": round(graph_score, 3),
                        "traceback": round(tb_score, 3),
                        "temporal": round(temp_score, 3),
                    },
                    provenance=prov,
                    docstring=node.docstring or "",
                )

        ranked = sorted(candidates_map.values(), key=lambda c: c.score, reverse=True)
        return ranked[:top_k]
