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
    commit = "unknown_commit"
    dirty_part = ""
    try:
        # ``git rev-parse HEAD`` walks up through parent directories.  That is
        # useful for a file inside a repository, but incorrect for a caller
        # that explicitly supplies a standalone directory nested beneath some
        # other checkout (as temporary test repos often are).  Only use Git
        # identity when this directory is the checkout root.
        root_res = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if (
            root_res.returncode != 0
            or not root_res.stdout.strip()
            or os.path.normcase(os.path.abspath(root_res.stdout.strip()))
            != os.path.normcase(os.path.abspath(repo_path))
        ):
            raise RuntimeError("repository path is not a Git checkout root")
        res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if res.returncode == 0 and res.stdout.strip():
            commit = res.stdout.strip()
            diff_res = subprocess.run(
                ["git", "diff", "HEAD"],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            # Use -uall so untracked subdirectories are expanded into individual files
            status_res = subprocess.run(
                ["git", "status", "--porcelain", "-uall"],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )

            # Untracked Python file contents: git diff HEAD does not track them,
            # and git status --porcelain output is static even if untracked file content changes.
            # Cryptographically hash contents of all untracked .py files so cache invalidation is sound.
            untracked_content_hashes = []
            if status_res.stdout:
                for line in status_res.stdout.splitlines():
                    line = line.strip()
                    if line.startswith("??"):
                        rel_file = line[2:].strip().strip('"')
                        if rel_file.endswith(".py"):
                            parts = set(rel_file.replace("\\", "/").split("/"))
                            if not parts.intersection(EXCLUDE_DIRS):
                                full_p = os.path.join(repo_path, rel_file)
                                if os.path.isfile(full_p):
                                    try:
                                        with open(full_p, "rb") as uf:
                                            h = hashlib.sha256(uf.read()).hexdigest()[:16]
                                        untracked_content_hashes.append(f"{rel_file}:{h}")
                                    except Exception:
                                        pass
                                else:
                                    untracked_content_hashes.append(f"{rel_file}:deleted")

            dirty_raw = (
                (diff_res.stdout or "")
                + "\n"
                + (status_res.stdout or "")
                + "\n"
                + "\n".join(sorted(untracked_content_hashes))
            )
            if dirty_raw.strip():
                dirty_part = ":" + hashlib.sha256(dirty_raw.encode("utf-8")).hexdigest()[:16]
    except Exception:
        pass

    # Non-git fallback: if git is unavailable or repo is not a git checkout,
    # hash all discoverable Python files so cache invalidation remains sound across content changes.
    if commit == "unknown_commit":
        try:
            py_fingerprints = []
            for root, dirs, files in os.walk(repo_path):
                dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS and not d.endswith(".egg-info")]
                for f in sorted(files):
                    if f.endswith(".py"):
                        full_f = os.path.join(root, f)
                        rel_f = os.path.relpath(full_f, repo_path).replace("\\", "/")
                        try:
                            with open(full_f, "rb") as pf:
                                pf_hash = hashlib.sha256(pf.read()).hexdigest()[:16]
                            py_fingerprints.append(f"{rel_f}:{pf_hash}")
                        except Exception:
                            pass
            if py_fingerprints:
                non_git_hash = hashlib.sha256("\n".join(py_fingerprints).encode("utf-8")).hexdigest()[:16]
                commit = f"nongit_{non_git_hash}"
        except Exception:
            pass

    return f"{commit}{dirty_part}"


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
        test_cache_path = cache_path.replace(".graph.json", ".test_index.json")
        test_index = TestGraphIndex()
        
        if not force_rebuild and os.path.exists(cache_path):
            try:
                logger.info(f"Loading cached repository intelligence graph from {cache_path}")
                graph = RepositoryGraph.load(cache_path)
                if os.path.exists(test_cache_path):
                    try:
                        test_index = TestGraphIndex.load(test_cache_path)
                        logger.info(f"Loaded cached test index from {test_cache_path} ({len(test_index.tests)} tests)")
                        return graph, test_index
                    except Exception as te:
                        logger.warning(f"Failed to load cached test index: {te}, re-indexing test files...")
                # Re-index test files quickly if test index cache missing/corrupt
                for node in graph.nodes.values():
                    if "test" in node.file_path.lower() and node.file_path.endswith(".py"):
                        full_p = os.path.join(self.repo_path, node.file_path)
                        if os.path.exists(full_p):
                            try:
                                with open(full_p, "r", encoding="utf-8", errors="replace") as f:
                                    test_index.index_test_file(node.file_path, f.read())
                            except Exception:
                                pass
                try:
                    test_index.save(test_cache_path)
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
            test_index.save(test_cache_path)
            logger.info(f"Test graph index cached successfully to {test_cache_path} ({len(test_index.tests)} tests)")
        except Exception as e:
            logger.warning(f"Failed to cache repository graph or test index: {e}")

        return graph, test_index
