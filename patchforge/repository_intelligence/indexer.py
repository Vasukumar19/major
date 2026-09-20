"""Repository Indexer: parallel AST indexing, graph construction, and disk caching."""
import concurrent.futures
import hashlib
import json
import logging
import os
import subprocess
from typing import List, Optional, Set, Tuple

from patchforge.repository_intelligence.graph import RepositoryGraph
from patchforge.repository_intelligence.parser import parse_source_file
from patchforge.repository_intelligence.test_graph import TestGraphIndex

logger = logging.getLogger(__name__)

EXCLUDE_DIRS = {
    ".git", ".pytest_cache", "__pycache__", "venv", ".venv", "env",
    "build", "dist", "node_modules", ".tox", ".eggs", "*.egg-info"
}


def _get_git_commit(repo_path: str) -> str:
    try:
        res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    except Exception:
        pass
    return "unknown_commit"


def _parse_single_file(args: Tuple[str, str]):
    full_path, rel_path = args
    try:
        with open(full_path, "r", encoding="utf-8", errors="replace") as f:
            code = f.read()
        nodes, edges = parse_source_file(rel_path, code)
        is_test = "test" in rel_path.lower()
        return rel_path, code, nodes, edges, is_test
    except Exception as e:
        logger.debug(f"Failed to parse {rel_path}: {e}")
        return rel_path, "", [], [], False


class RepositoryIndexer:
    """Orchestrates parallel parsing and indexing of an entire repository."""

    def __init__(self, repo_path: str, cache_dir: str = ".patchforge_cache"):
        self.repo_path = os.path.abspath(repo_path)
        self.cache_dir = os.path.abspath(cache_dir)
        self.commit_hash = _get_git_commit(self.repo_path)
        self.repo_name = os.path.basename(self.repo_path)

    def _get_cache_path(self) -> str:
        h = hashlib.sha256(f"{self.repo_path}:{self.commit_hash}".encode()).hexdigest()[:16]
        return os.path.join(self.cache_dir, f"{self.repo_name}_{h}.graph.json")

    def index(self, force_rebuild: bool = False, max_workers: int = 8) -> Tuple[RepositoryGraph, TestGraphIndex]:
        cache_path = self._get_cache_path()
        test_index = TestGraphIndex()
        
        if not force_rebuild and os.path.exists(cache_path):
            try:
                logger.info(f"Loading cached repository intelligence graph from {cache_path}")
                graph = RepositoryGraph.load(cache_path)
                # Re-index test files quickly
                for node in graph.nodes.values():
                    if "test" in node.file_path.lower() and node.file_path.endswith(".py"):
                        full_p = os.path.join(self.repo_path, node.file_path)
                        if os.path.exists(full_p):
                            try:
                                with open(full_p, "r", encoding="utf-8", errors="replace") as f:
                                    test_index.index_test_file(node.file_path, f.read())
                            except Exception:
                                pass
                return graph, test_index
            except Exception as e:
                logger.warning(f"Error loading cached graph from {cache_path}: {e}, rebuilding...")

        logger.info(f"Building repository intelligence index for {self.repo_path} ({self.commit_hash})...")
        graph = RepositoryGraph(repo_name=self.repo_name)
        
        # Discover all Python files
        py_files: List[Tuple[str, str]] = []
        for root, dirs, files in os.walk(self.repo_path):
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS and not d.endswith(".egg-info")]
            for file in files:
                if file.endswith(".py"):
                    full_path = os.path.join(root, file)
                    rel_path = os.path.relpath(full_path, self.repo_path).replace("\\", "/")
                    py_files.append((full_path, rel_path))

        # Parallel AST parsing
        workers = min(max_workers, os.cpu_count() or 4)
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            results = executor.map(_parse_single_file, py_files)

        for rel_path, code, nodes, edges, is_test in results:
            for node in nodes:
                graph.add_node(node)
            for edge in edges:
                graph.add_edge(edge)
            if is_test and code:
                test_index.index_test_file(rel_path, code)

        # Save to disk cache
        try:
            graph.save(cache_path)
            logger.info(f"Repository graph cached successfully to {cache_path} ({len(graph.nodes)} nodes, {graph.g.number_of_edges()} edges)")
        except Exception as e:
            logger.warning(f"Failed to cache repository graph: {e}")

        return graph, test_index
