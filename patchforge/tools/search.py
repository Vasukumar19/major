"""Search and symbol navigation tools for PatchForge v0.3."""
from __future__ import annotations

import ast
import os
import re
from pathlib import Path
from typing import Any

from patchforge.tools.base import Tool, ToolResult
from patchforge.tools.editor import _resolve_and_contain

IGNORED_DIRS = {
    ".git", "__pycache__", "venv", ".venv", "env",
    "build", "dist", "third_party", ".pytest_cache", ".tox", "node_modules", ".eggs"
}


class SearchCodeTool(Tool):
    name = "search_code"
    description = (
        "Search code across the repository matching a regex or query pattern. "
        "Returns matching files, line numbers, and snippets."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Regex or search substring pattern"},
            "file_filter": {"type": "string", "description": "Optional glob pattern or directory prefix to restrict search (e.g. '*.py' or 'requests/')"},
            "max_matches": {"type": "integer", "description": "Max number of match snippets to return (default 15)"},
        },
        "required": ["query"],
    }

    def __init__(self, repo_dir: str = ""):
        self.repo_dir = repo_dir

    def execute(self, args: dict[str, Any], state: Any = None) -> ToolResult:
        query = args.get("query", "")
        if not query:
            return ToolResult(self.name, status="ERROR", error="Query cannot be empty.")
        
        file_filter = args.get("file_filter", "")
        max_matches = args.get("max_matches", 15)
        repo_path = Path(self.repo_dir or (state.repo_dir if state and hasattr(state, "repo_dir") else "."))

        try:
            pattern = re.compile(query, re.IGNORECASE)
        except re.error as e:
            return ToolResult(self.name, status="ERROR", error=f"Invalid regex '{query}': {str(e)}")

        matches = []
        for root, dirs, files in os.walk(repo_path):
            dirs[:] = sorted([d for d in dirs if d not in IGNORED_DIRS])
            files.sort()
            for file in files:
                if not file.endswith(".py"):
                    continue
                full_path = Path(root) / file
                rel_path = str(full_path.relative_to(repo_path)).replace("\\", "/")
                
                if file_filter and file_filter not in rel_path:
                    continue

                try:
                    with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                        for lineno, line in enumerate(f, 1):
                            if pattern.search(line):
                                matches.append({
                                    "file": rel_path,
                                    "line_number": lineno,
                                    "snippet": line.strip()[:200],
                                })
                                if len(matches) >= max_matches:
                                    break
                except Exception:
                    continue
                if len(matches) >= max_matches:
                    break
            if len(matches) >= max_matches:
                break

        return ToolResult(
            tool_name=self.name,
            status="SUCCESS",
            data={"query": query, "matches": matches, "total_matches": len(matches)},
            message=f"Found {len(matches)} matches for '{query}'.",
        )


class SearchExactTool(Tool):
    name = "search_exact"
    description = (
        "Exact literal search across files in the repository. "
        "Useful for locating exact error messages, identifiers, or URLs."
    )
    parameters = {
        "type": "object",
        "properties": {
            "literal": {"type": "string", "description": "Exact text substring to find"},
            "file_filter": {"type": "string", "description": "Optional file path filter"},
            "max_matches": {"type": "integer", "description": "Max matches to return (default 15)"},
        },
        "required": ["literal"],
    }

    def __init__(self, repo_dir: str = ""):
        self.repo_dir = repo_dir

    def execute(self, args: dict[str, Any], state: Any = None) -> ToolResult:
        literal = args.get("literal", "")
        if not literal:
            return ToolResult(self.name, status="ERROR", error="Literal cannot be empty.")
        
        file_filter = args.get("file_filter", "")
        max_matches = args.get("max_matches", 15)
        repo_path = Path(self.repo_dir or (state.repo_dir if state and hasattr(state, "repo_dir") else "."))

        matches = []
        for root, dirs, files in os.walk(repo_path):
            dirs[:] = sorted([d for d in dirs if d not in IGNORED_DIRS])
            files.sort()
            for file in files:
                if not file.endswith(".py"):
                    continue
                full_path = Path(root) / file
                rel_path = str(full_path.relative_to(repo_path)).replace("\\", "/")
                
                if file_filter and file_filter not in rel_path:
                    continue

                try:
                    with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                        for lineno, line in enumerate(f, 1):
                            if literal in line:
                                matches.append({
                                    "file": rel_path,
                                    "line_number": lineno,
                                    "snippet": line.strip()[:200],
                                })
                                if len(matches) >= max_matches:
                                    break
                except Exception:
                    continue
                if len(matches) >= max_matches:
                    break
            if len(matches) >= max_matches:
                break

        return ToolResult(
            tool_name=self.name,
            status="SUCCESS",
            data={"literal": literal, "matches": matches, "total_matches": len(matches)},
            message=f"Found {len(matches)} exact matches for '{literal}'.",
        )


class FindSymbolTool(Tool):
    name = "find_symbol"
    description = (
        "Locate definition of a function, method, or class symbol via AST inspection."
    )
    parameters = {
        "type": "object",
        "properties": {
            "symbol_name": {"type": "string", "description": "Name of the class, function, or method to find"},
            "file_path": {"type": "string", "description": "Optional specific file to inspect"},
        },
        "required": ["symbol_name"],
    }

    def __init__(self, repo_dir: str = ""):
        self.repo_dir = repo_dir

    def execute(self, args: dict[str, Any], state: Any = None) -> ToolResult:
        symbol_name = args.get("symbol_name", "")
        file_path = args.get("file_path", "")
        repo_path = Path(self.repo_dir or (state.repo_dir if state and hasattr(state, "repo_dir") else "."))

        exact_symbols = []
        substring_symbols = []
        files_to_check = []

        if file_path:
            resolved_file, err = _resolve_and_contain(repo_path, file_path, must_exist=False)
            if err:
                return ToolResult(self.name, status="ERROR", error=err)
            if resolved_file and resolved_file.exists():
                rel = str(resolved_file.relative_to(repo_path.resolve())).replace("\\", "/")
                files_to_check.append((rel, resolved_file))
        else:
            for root, dirs, files in os.walk(repo_path):
                dirs[:] = sorted([d for d in dirs if d not in IGNORED_DIRS])
                files.sort()
                for file in files:
                    if file.endswith(".py"):
                        full = Path(root) / file
                        rel = str(full.relative_to(repo_path)).replace("\\", "/")
                        files_to_check.append((rel, full))

        for rel, full in files_to_check:
            try:
                with open(full, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
                tree = ast.parse(content, filename=str(full))
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        sym_info = {
                            "symbol": node.name,
                            "type": "class" if isinstance(node, ast.ClassDef) else "function",
                            "file": rel,
                            "line_start": node.lineno,
                            "line_end": getattr(node, "end_lineno", node.lineno + 10),
                        }
                        if node.name == symbol_name:
                            exact_symbols.append(sym_info)
                        elif symbol_name in node.name:
                            substring_symbols.append(sym_info)
            except Exception:
                continue

        exact_symbols.sort(key=lambda s: (s["file"], s["line_start"], s["symbol"]))
        substring_symbols.sort(key=lambda s: (s["file"], s["line_start"], s["symbol"]))
        found_symbols = (exact_symbols + substring_symbols)[:10]

        return ToolResult(
            tool_name=self.name,
            status="SUCCESS",
            data={"symbol_name": symbol_name, "symbols": found_symbols},
            message=f"Found {len(found_symbols)} definitions for symbol '{symbol_name}'.",
        )


class FindReferencesTool(Tool):
    name = "find_references"
    description = (
        "Find calls and references to a specific symbol across the codebase."
    )
    parameters = {
        "type": "object",
        "properties": {
            "symbol_name": {"type": "string", "description": "Symbol name to find references for"},
            "max_references": {"type": "integer", "description": "Max references to return (default 15)"},
        },
        "required": ["symbol_name"],
    }

    def __init__(self, repo_dir: str = ""):
        self.repo_dir = repo_dir

    def execute(self, args: dict[str, Any], state: Any = None) -> ToolResult:
        symbol_name = args.get("symbol_name", "")
        max_refs = args.get("max_references", 15)
        repo_path = Path(self.repo_dir or (state.repo_dir if state and hasattr(state, "repo_dir") else "."))

        # Use regex identifier match
        pattern = re.compile(rf"\b{re.escape(symbol_name)}\b")
        references = []

        for root, dirs, files in os.walk(repo_path):
            dirs[:] = sorted([d for d in dirs if d not in IGNORED_DIRS])
            files.sort()
            for file in files:
                if not file.endswith(".py"):
                    continue
                full_path = Path(root) / file
                rel_path = str(full_path.relative_to(repo_path)).replace("\\", "/")

                try:
                    with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                        for lineno, line in enumerate(f, 1):
                            if pattern.search(line):
                                references.append({
                                    "file": rel_path,
                                    "line_number": lineno,
                                    "snippet": line.strip()[:200],
                                })
                                if len(references) >= max_refs:
                                    break
                except Exception:
                    continue
                if len(references) >= max_refs:
                    break
            if len(references) >= max_refs:
                break

        return ToolResult(
            tool_name=self.name,
            status="SUCCESS",
            data={"symbol_name": symbol_name, "references": references},
            message=f"Found {len(references)} references to '{symbol_name}'.",
        )
