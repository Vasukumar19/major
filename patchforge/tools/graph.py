"""Graph and history navigation tools for PatchForge v0.3 with multi-tier fallback."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from patchforge.tools.base import Tool, ToolResult


class InspectGraphTool(Tool):
    name = "inspect_graph"
    description = (
        "Inspect code relationships, callers, callees, and symbol graph neighbors. "
        "Supports full graph, targeted subgraph, and graceful symbol fallback."
    )
    parameters = {
        "type": "object",
        "properties": {
            "symbol": {"type": "string", "description": "Symbol name or function/class to query"},
            "query_type": {
                "type": "string",
                "enum": ["neighbors", "callers", "callees", "hierarchy"],
                "description": "Graph relation query type",
            },
            "depth": {"type": "integer", "description": "Graph traversal depth (default 1)"},
        },
        "required": ["symbol"],
    }

    def __init__(self, repo_dir: str = "", repograph_adapter: Any = None):
        self.repo_dir = repo_dir
        self.repograph_adapter = repograph_adapter

    def execute(self, args: dict[str, Any], state: Any = None) -> ToolResult:
        symbol = args.get("symbol", "")
        query_type = args.get("query_type", "neighbors")
        depth = args.get("depth", 1)

        repo_path = Path(self.repo_dir or (state.repo_dir if state and hasattr(state, "repo_dir") else "."))

        # Check if adapter is provided
        adapter = self.repograph_adapter or (state.repograph_adapter if state and hasattr(state, "repograph_adapter") else None)

        if adapter and hasattr(state, "instance_id") and state.instance_id:
            try:
                # Try targeted or full query
                graph_pkl = adapter.graph_path(state.instance_id, "cache/repograph")
                if Path(graph_pkl).exists():
                    ctx = adapter.related_symbols(graph_pkl, symbol, depth=depth)
                    return ToolResult(
                        self.name,
                        status="SUCCESS",
                        data={"symbol": symbol, "relations": ctx.related, "query_type": query_type, "mode": "FULL_GRAPH"},
                        message=f"Found {len(ctx.related)} related symbols via RepoGraph.",
                    )
            except Exception:
                pass  # Fall through to fallback

        # Fallback: lightweight AST neighbor scanning
        related = []
        for root, _, files in os.walk(repo_path):
            for file in files:
                if not file.endswith(".py"):
                    continue
                full = Path(root) / file
                rel = str(full.relative_to(repo_path)).replace("\\", "/")
                try:
                    with open(full, "r", encoding="utf-8", errors="replace") as f:
                        text = f.read()
                    if symbol in text:
                        # Extract simple function defs in the same file
                        import ast
                        tree = ast.parse(text)
                        for node in ast.walk(tree):
                            if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name != symbol:
                                related.append(f"{rel}::{node.name}")
                                if len(related) >= 10:
                                    break
                except Exception:
                    continue
                if len(related) >= 10:
                    break
            if len(related) >= 10:
                break

        return ToolResult(
            tool_name=self.name,
            status="SUCCESS",
            data={"symbol": symbol, "relations": related, "query_type": query_type, "mode": "NO_GRAPH_FALLBACK"},
            message=f"Found {len(related)} related symbols via AST fallback.",
        )


class InspectGitHistoryTool(Tool):
    name = "inspect_git_history"
    description = "Inspect recent git commit messages and changes touching a file or symbol."
    parameters = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "File path to inspect in git history"},
            "max_commits": {"type": "integer", "description": "Number of recent commits (default 5)"},
        },
        "required": ["file_path"],
    }

    def __init__(self, repo_dir: str = ""):
        self.repo_dir = repo_dir

    def execute(self, args: dict[str, Any], state: Any = None) -> ToolResult:
        file_path = args.get("file_path", "")
        max_commits = args.get("max_commits", 5)
        repo_path = Path(self.repo_dir or (state.repo_dir if state and hasattr(state, "repo_dir") else "."))

        try:
            cmd = ["git", "log", f"-n{max_commits}", "--oneline", "--", file_path]
            proc = subprocess.run(cmd, cwd=str(repo_path), capture_output=True, text=True, timeout=10)
            log_output = proc.stdout.strip()
            return ToolResult(
                self.name,
                status="SUCCESS",
                data={"file_path": file_path, "log": log_output},
                message=f"Retrieved git history for {file_path}.",
                raw_output=log_output,
            )
        except Exception as e:
            return ToolResult(
                self.name,
                status="ERROR",
                error=f"Git history lookup failed: {str(e)}",
            )
