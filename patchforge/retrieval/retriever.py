"""Targeted code retrieval from a base_commit checkout. No LLM, no index."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class CodeContext:
    file: str = ""
    symbol: str = ""
    start_line: int = 0
    end_line: int = 0
    text: str = ""


class Retriever:
    def __init__(self, repo_dir: str):
        self.repo_dir = repo_dir

    def read_file(self, path: str, max_lines: int = 2000) -> str:
        full = Path(self.repo_dir, path)
        if not full.is_file():
            return ""
        lines = full.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(f"{i + 1}:{line}" for i, line in enumerate(lines[:max_lines]))

    def read_raw(self, path: str) -> str:
        """Full file content without line numbers (for patch application)."""
        full = Path(self.repo_dir, path)
        if not full.is_file():
            return ""
        return full.read_text(encoding="utf-8", errors="replace")

    def symbol_context(self, path: str, symbol: str, window: int = 30) -> CodeContext:
        """Excerpt around `def|class <symbol>` with line numbers."""
        full = Path(self.repo_dir, path)
        if not full.is_file():
            return CodeContext(file=path, symbol=symbol)
        lines = full.read_text(encoding="utf-8", errors="replace").splitlines()
        pat = re.compile(rf"^\s*(def|class)\s+{re.escape(symbol)}\b")
        hits = [i for i, line in enumerate(lines) if pat.match(line)]
        if not hits:
            return CodeContext(file=path, symbol=symbol)
        start = max(0, hits[0] - window)
        end = min(len(lines), hits[-1] + window + 1)
        text = "\n".join(f"{i + 1}:{lines[i]}" for i in range(start, end))
        return CodeContext(file=path, symbol=symbol,
                           start_line=start + 1, end_line=end, text=text)

    def file_symbols(self, path: str) -> list[str]:
        """def/class names in a file, including methods (cheap, regex-based)."""
        full = Path(self.repo_dir, path)
        if not full.is_file():
            return []
        pat = re.compile(r"^\s*(?:def|class)\s+(\w+)")
        names = [m.group(1) for line in
                 full.read_text(encoding="utf-8", errors="replace").splitlines()
                 if (m := pat.match(line))]
        return list(dict.fromkeys(names))
