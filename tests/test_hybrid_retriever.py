"""Unit tests for Hybrid Repository Retriever."""
from __future__ import annotations

import tempfile
from pathlib import Path
from patchforge.repository_intelligence.graph import RepositoryGraph
from patchforge.repository_intelligence.schema import EdgeKind, NodeKind, GraphNode
from patchforge.retrieval.hybrid import HybridRepositoryRetriever, RetrievedCandidate


def test_hybrid_retriever():
    with tempfile.TemporaryDirectory() as tmpdir:
        rg = RepositoryGraph(tmpdir)
        n1 = GraphNode(
            id="f1:Session.merge_environment_settings",
            name="Session.merge_environment_settings",
            kind=NodeKind.METHOD,
            file_path="requests/sessions.py",
            start_line=10,
            end_line=50,
            docstring="Merge environment settings into session proxies.",
        )
        rg.add_node(n1)

        retriever = HybridRepositoryRetriever(tmpdir, code_graph=rg)
        cands = retriever.retrieve(
            query="session environment settings proxies merge",
            traceback_text="Traceback ... in merge_environment_settings",
            top_k=5,
        )

        assert len(cands) == 1
        cand = cands[0]
        assert cand.symbol == "Session.merge_environment_settings"
        assert cand.score > 0.0
        assert "lexical" in cand.scores_by_channel
        assert "traceback" in cand.scores_by_channel
        assert cand.scores_by_channel["traceback"] == 1.0
        assert "Format" not in cand.format_summary()
        assert "Candidate" in cand.format_summary()
