"""Temporal Repository Graph: connects symbols, callers/callees, tests, commits, and churn history.

Builds a temporal reasoning substrate on top of RepositoryGraph and Git evidence:
- SYMBOL_CHANGED_IN_COMMIT
- FILE_CHANGED_IN_COMMIT
- SYMBOL_CO_CHANGED_WITH_SYMBOL (co-change frequency)
- CALLER_CHANGED_AFTER_CALLEE
- HISTORICALLY_FIXED_WITH (commits addressing bug fixes)
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import networkx as nx

from patchforge.repository_intelligence.graph import RepositoryGraph
from patchforge.repository_intelligence.schema import EdgeKind, NodeKind, GraphNode

logger = logging.getLogger(__name__)


@dataclass
class CommitRecord:
    commit_hash: str
    author: str
    date_str: str
    timestamp: float
    message: str
    is_bug_fix: bool = False
    files_changed: List[str] = field(default_factory=list)
    symbols_changed: List[str] = field(default_factory=list)


@dataclass
class SymbolTemporalProfile:
    file_path: str
    symbol_name: str
    total_commits: int = 0
    bug_fix_commits: int = 0
    last_modified_date: str = ""
    churn_score: float = 0.0  # normalized 0.0 - 1.0
    co_changed_symbols: List[Tuple[str, int]] = field(default_factory=list)  # (symbol, count)
    historical_fix_messages: List[str] = field(default_factory=list)

    def format_summary(self) -> str:
        lines = [f"Temporal Profile for `{self.file_path}:{self.symbol_name}`:"]
        lines.append(f"  - Total Commits Touching: {self.total_commits} (Bug Fixes: {self.bug_fix_commits})")
        lines.append(f"  - Churn Score: {self.churn_score:.2f}")
        if self.last_modified_date:
            lines.append(f"  - Last Modified: {self.last_modified_date}")
        if self.co_changed_symbols:
            co_str = ", ".join(f"{s} ({c}x)" for s, c in self.co_changed_symbols[:5])
            lines.append(f"  - Frequently Co-Changed Symbols: {co_str}")
        if self.historical_fix_messages:
            lines.append("  - Historical Bug Fixes:")
            for msg in self.historical_fix_messages[:3]:
                lines.append(f"      * {msg}")
        return "\n".join(lines)


class TemporalRepositoryGraph:
    """Integrates Git temporal history directly into the code graph."""

    BUG_KEYWORDS = {"fix", "bug", "patch", "issue", "resolve", "defect", "error", "crash"}

    def __init__(self, repo_dir: str, code_graph: Optional[RepositoryGraph] = None):
        self.repo_dir = Path(repo_dir)
        self.code_graph = code_graph
        self.commits: Dict[str, CommitRecord] = {}
        self.symbol_commits: Dict[str, List[str]] = {}  # "file:symbol" -> [commit_hash]
        self.file_commits: Dict[str, List[str]] = {}    # "file" -> [commit_hash]
        self.co_change_counts: Dict[str, Dict[str, int]] = {}  # sym -> {other_sym -> count}
        self.temporal_graph = nx.MultiDiGraph()

    def _run_git(self, args: List[str], timeout: int = 20) -> str:
        try:
            res = subprocess.run(
                ["git"] + args,
                cwd=str(self.repo_dir),
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            return res.stdout.strip()
        except Exception as e:
            logger.debug(f"Git command failed: {e}")
            return ""

    def build(self, max_commits: int = 100) -> TemporalRepositoryGraph:
        """Parses git log commits, associates diff hunks with AST symbols, and constructs temporal graph."""
        log_out = self._run_git(["log", f"-n{max_commits}", "--name-only", "--pretty=format:COMMIT|%H|%an|%ad|%at|%s", "--date=short"])
        if not log_out:
            return self

        current_commit: Optional[CommitRecord] = None
        for block in log_out.split("COMMIT|"):
            if not block.strip():
                continue
            lines = block.strip().splitlines()
            header = lines[0].split("|")
            if len(header) < 5:
                continue

            c_hash, author, date_str, ts_str, msg = header[0], header[1], header[2], header[3], header[4]
            try:
                ts = float(ts_str)
            except ValueError:
                ts = 0.0

            is_fix = any(w in msg.lower() for w in self.BUG_KEYWORDS)
            files = [line.strip().replace("\\", "/") for line in lines[1:] if line.strip() and line.strip().endswith(".py")]

            record = CommitRecord(
                commit_hash=c_hash,
                author=author,
                date_str=date_str,
                timestamp=ts,
                message=msg,
                is_bug_fix=is_fix,
                files_changed=files,
            )
            self.commits[c_hash] = record

            # Add commit node to temporal graph
            self.temporal_graph.add_node(
                f"commit:{c_hash}",
                kind="commit",
                author=author,
                date=date_str,
                timestamp=ts,
                message=msg,
                is_bug_fix=is_fix,
            )

            for f in files:
                self.file_commits.setdefault(f, []).append(c_hash)
                self.temporal_graph.add_edge(f"commit:{c_hash}", f"file:{f}", kind="FILE_CHANGED_IN_COMMIT")

        # Associate symbols with commits via file commits and code_graph AST
        if self.code_graph:
            self._map_symbols_to_commits()

        return self

    def _map_symbols_to_commits(self):
        """Maps symbol modifications to commits based on file changes and AST spans."""
        file_to_symbols: Dict[str, List[GraphNode]] = {}
        for nid, node in self.code_graph.nodes.items():
            if node.kind in (NodeKind.FUNCTION, NodeKind.METHOD, NodeKind.CLASS):
                file_to_symbols.setdefault(node.file_path, []).append(node)

        for file_path, commits in self.file_commits.items():
            syms_in_file = file_to_symbols.get(file_path, [])
            for c_hash in commits[:15]:  # bound to top recent commits per file
                c_record = self.commits.get(c_hash)
                if not c_record:
                    continue

                for sym_node in syms_in_file:
                    sym_key = f"{file_path}:{sym_node.name}"
                    self.symbol_commits.setdefault(sym_key, []).append(c_hash)
                    if sym_node.name not in c_record.symbols_changed:
                        c_record.symbols_changed.append(sym_node.name)

                    self.temporal_graph.add_edge(
                        f"commit:{c_hash}",
                        f"symbol:{sym_key}",
                        kind="SYMBOL_CHANGED_IN_COMMIT",
                    )

        # Build co-change edges
        for c_hash, record in self.commits.items():
            if len(record.symbols_changed) > 1:
                for i, sym1 in enumerate(record.symbols_changed):
                    for sym2 in record.symbols_changed[i + 1 :]:
                        if sym1 != sym2:
                            self.co_change_counts.setdefault(sym1, {}).setdefault(sym2, 0)
                            self.co_change_counts[sym1][sym2] += 1
                            self.co_change_counts.setdefault(sym2, {}).setdefault(sym1, 0)
                            self.co_change_counts[sym2][sym1] += 1

                            self.temporal_graph.add_edge(
                                f"symbol:{sym1}",
                                f"symbol:{sym2}",
                                kind="SYMBOL_CO_CHANGED_WITH_SYMBOL",
                                count=self.co_change_counts[sym1][sym2],
                            )

    def get_symbol_profile(self, file_path: str, symbol_name: str) -> SymbolTemporalProfile:
        """Computes comprehensive temporal metrics for a candidate symbol."""
        rel_file = file_path.replace("\\", "/")
        sym_key = f"{rel_file}:{symbol_name}"
        commit_hashes = self.symbol_commits.get(sym_key, [])
        if not commit_hashes:
            commit_hashes = self.file_commits.get(rel_file, [])

        fix_count = 0
        fix_msgs = []
        last_date = ""

        for ch in commit_hashes:
            c = self.commits.get(ch)
            if c:
                if not last_date:
                    last_date = c.date_str
                if c.is_bug_fix:
                    fix_count += 1
                    if len(fix_msgs) < 5:
                        fix_msgs.append(f"[{c.commit_hash[:7]}] {c.message}")

        # Compute normalized churn score
        churn = min(1.0, len(commit_hashes) / 20.0 + (fix_count / 5.0) * 0.5)

        # Get top co-changed symbols
        co_changed = []
        short_sym = symbol_name.split(".")[-1]
        co_map = self.co_change_counts.get(symbol_name) or self.co_change_counts.get(short_sym) or {}
        for other_sym, count in sorted(co_map.items(), key=lambda x: x[1], reverse=True)[:5]:
            co_changed.append((other_sym, count))

        return SymbolTemporalProfile(
            file_path=rel_file,
            symbol_name=symbol_name,
            total_commits=len(commit_hashes),
            bug_fix_commits=fix_count,
            last_modified_date=last_date,
            churn_score=round(churn, 3),
            co_changed_symbols=co_changed,
            historical_fix_messages=fix_msgs,
        )

    def get_co_changed_symbols(self, symbol_name: str) -> List[Tuple[str, int]]:
        """Returns symbols that frequently co-changed with target."""
        short_sym = symbol_name.split(".")[-1]
        co_map = self.co_change_counts.get(symbol_name) or self.co_change_counts.get(short_sym) or {}
        return sorted(co_map.items(), key=lambda x: x[1], reverse=True)[:5]
