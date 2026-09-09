"""Thin adapter over RepoGraph (in-process queries, build-once graph cache).

Exposes: related_symbols/files, callers/callees, imports, references.
Returns normalized PatchForge Evidence objects (never raw graph internals).
Graph build shells out ONCE per repo commit; queries are in-process.
Supports FULL_GRAPH, TARGETED_GRAPH, and NO_GRAPH_FALLBACK modes.
"""
from __future__ import annotations

import json
import os
import pickle
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from patchforge.core.config import PatchForgeConfig
from patchforge.retrieval.evidence import Evidence, EvidenceType


class GraphMode(str, Enum):
    FULL_GRAPH = "FULL_GRAPH"
    TARGETED_GRAPH = "TARGETED_GRAPH"
    NO_GRAPH_FALLBACK = "NO_GRAPH_FALLBACK"


@dataclass
class GraphContext:
    symbol: str = ""
    related: list[str] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    mode: GraphMode = GraphMode.FULL_GRAPH


class RepoGraphAdapter:
    def __init__(self, config: PatchForgeConfig | None = None, third_party: str = "third_party"):
        self.config = config or PatchForgeConfig()
        self.repograph = str((Path(third_party) / "RepoGraph").resolve())
        self.available = Path(self.repograph, "repograph", "construct_graph.py").exists()
        if self.available:
            import sys
            if self.repograph not in sys.path:
                sys.path.insert(0, self.repograph)

    def graph_path(self, instance_id: str, cache_dir: str) -> str:
        return str(Path(cache_dir) / f"{instance_id}.graph.pkl")

    def tags_path(self, instance_id: str, cache_dir: str) -> str:
        return str(Path(cache_dir) / f"{instance_id}.tags.json")

    def ensure_graph(
        self,
        repo_dir: str,
        instance_id: str,
        cache_dir: str,
        timeout: int = 120,
        mode: GraphMode = GraphMode.FULL_GRAPH,
    ) -> str:
        """Build the graph once per instance; reuse the cached .pkl afterwards.
        Gracefully falls back on timeout or failure.
        """
        dest = self.graph_path(instance_id, cache_dir)
        if Path(dest).exists():
            return dest
        
        if mode == GraphMode.NO_GRAPH_FALLBACK or not self.available:
            return ""

        Path(cache_dir).mkdir(parents=True, exist_ok=True)
        repo_dir = str(Path(repo_dir).resolve())

        try:
            proc = subprocess.run(
                ["python", str(Path(self.repograph, "repograph", "construct_graph.py")), repo_dir],
                capture_output=True, text=True, cwd=cache_dir, timeout=timeout,
            )
            if proc.returncode != 0:
                return ""
            built = Path(cache_dir, "graph.pkl")
            tags = Path(cache_dir, "tags.json")
            if built.exists():
                built.rename(dest)
            if tags.exists():
                tags.rename(Path(cache_dir) / f"{instance_id}.tags.json")
            return dest
        except (subprocess.TimeoutExpired, Exception):
            return ""

    def _searcher(self, graph_pkl: str):
        if not graph_pkl or not Path(graph_pkl).exists():
            return None
        from repograph.graph_searcher import RepoSearcher
        with open(graph_pkl, "rb") as f:
            return RepoSearcher(pickle.load(f))

    def _neighbors(self, graph_pkl: str, symbol: str, depth: int = 1) -> list[str]:
        try:
            s = self._searcher(graph_pkl)
            if not s:
                return []
            if depth <= 1:
                return sorted(set(s.one_hop_neighbors(symbol)))
            return sorted(set(s.two_hop_neighbors(symbol)))
        except Exception:
            return []

    def related_symbols(self, graph_pkl: str, symbol: str, depth: int = 1) -> GraphContext:
        rel = self._neighbors(graph_pkl, symbol, depth)
        mode = GraphMode.FULL_GRAPH if rel else GraphMode.NO_GRAPH_FALLBACK
        return GraphContext(
            symbol=symbol,
            related=rel,
            mode=mode,
            evidence=[
                Evidence(
                    source="repograph",
                    type=EvidenceType.GRAPH_RELATION.value,
                    symbol=r,
                    relevance=1.0 / (1.0 + i * 0.1),
                    explanation=f"graph neighbor of {symbol} (depth {depth})",
                )
                for i, r in enumerate(rel)
            ],
        )

    def callers_callees(self, graph_pkl: str, symbol: str) -> GraphContext:
        rel = self._neighbors(graph_pkl, symbol, depth=1)
        mode = GraphMode.FULL_GRAPH if rel else GraphMode.NO_GRAPH_FALLBACK
        return GraphContext(
            symbol=symbol,
            related=rel,
            mode=mode,
            evidence=[
                Evidence(
                    source="repograph",
                    type=EvidenceType.CALL_RELATION.value,
                    symbol=r,
                    relevance=0.8,
                    explanation=f"call/def relation with {symbol}",
                )
                for r in rel
            ],
        )

    def related_files(self, graph_pkl: str, symbol: str) -> GraphContext:
        if not graph_pkl or not Path(graph_pkl).exists():
            return GraphContext(symbol=symbol, related=[], mode=GraphMode.NO_GRAPH_FALLBACK)

        stem = Path(graph_pkl).name
        if stem.endswith(".graph.pkl"):
            stem = stem[: -len(".graph.pkl")]
        tags_file = Path(graph_pkl).parent / f"{stem}.tags.json"
        files: list[str] = []
        try:
            for line in tags_file.read_text(encoding="utf-8").splitlines():
                d = json.loads(line)
                if d.get("name") == symbol and d.get("rel_fname"):
                    files.append(d["rel_fname"])
        except FileNotFoundError:
            pass
        files = sorted(set(files))
        return GraphContext(
            symbol=symbol,
            related=files,
            mode=GraphMode.FULL_GRAPH if files else GraphMode.NO_GRAPH_FALLBACK,
            evidence=[
                Evidence(
                    source="repograph",
                    type=EvidenceType.SYMBOL_MATCH.value,
                    file=f,
                    symbol=symbol,
                    relevance=0.9,
                    explanation=f"{symbol} defined/referenced in {f}",
                )
                for f in files
            ],
        )

    imports = related_symbols
    references = related_symbols
