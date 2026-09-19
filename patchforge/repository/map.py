"""Aider-style compact repository mapping and context selection."""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class SymbolInfo:
    name: str
    kind: str  # "class", "function", "method"
    line_start: int
    line_end: int
    file_path: str
    signature: str = ""


class RepoMap:
    """Builds a concise map of classes, functions, and signatures for repository files."""

    def __init__(self, repo_dir: str):
        self.repo_dir = Path(repo_dir)

    def extract_symbols(self, file_path: str) -> list[SymbolInfo]:
        full_path = self.repo_dir / file_path
        if not full_path.exists() or not full_path.is_file():
            return []

        try:
            content = full_path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(content, filename=str(full_path))
        except Exception:
            return []

        symbols = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                symbols.append(SymbolInfo(
                    name=node.name,
                    kind="class",
                    line_start=node.lineno,
                    line_end=getattr(node, "end_lineno", node.lineno),
                    file_path=file_path,
                ))
            elif isinstance(node, ast.FunctionDef) or isinstance(node, getattr(ast, "AsyncFunctionDef", ())):
                args = [a.arg for a in node.args.args]
                sig = f"def {node.name}({', '.join(args)})"
                symbols.append(SymbolInfo(
                    name=node.name,
                    kind="function",
                    line_start=node.lineno,
                    line_end=getattr(node, "end_lineno", node.lineno),
                    file_path=file_path,
                    signature=sig,
                ))
        return symbols

    def find_symbol_location(self, file_path: str, symbol_name: str) -> tuple[int, int]:
        """Find the start and end line of a symbol within a file."""
        symbols = self.extract_symbols(file_path)
        clean_name = symbol_name.split(".")[-1].strip().lower()
        for s in symbols:
            if s.name.lower() == clean_name:
                return s.line_start, s.line_end
        return 1, 100

    def get_source_window(
        self,
        file_path: str,
        symbol_name: str = "",
        padding_before: int = 15,
        padding_after: int = 80,
    ) -> tuple[str, int, int]:
        """Extract exact lines centered on symbol with line numbers."""
        full_path = self.repo_dir / file_path
        if not full_path.exists():
            return "", 0, 0

        try:
            content = full_path.read_text(encoding="utf-8", errors="replace")
            lines = content.splitlines(keepends=True)
        except Exception:
            return "", 0, 0

        total_lines = len(lines)
        if not total_lines:
            return "", 0, 0

        s_start, s_end = self.find_symbol_location(file_path, symbol_name)
        if s_start > 1 or symbol_name:
            win_start = max(1, s_start - padding_before)
            win_end = min(total_lines, s_end + padding_after)
        else:
            win_start = 1
            win_end = min(total_lines, 120)

        window_lines = lines[win_start - 1 : win_end]
        verbatim_snippet = "".join(window_lines)
        return verbatim_snippet, win_start, win_end
