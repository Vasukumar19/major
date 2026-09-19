"""Aider-style compact repository mapping and context selection with surgical unit extraction."""
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
    parent_class: str = ""
    docstring: str = ""


class _SymbolVisitor(ast.NodeVisitor):
    def __init__(self, file_path: str):
        self.file_path = file_path
        self.symbols: list[SymbolInfo] = []
        self.current_class: str = ""

    def visit_ClassDef(self, node: ast.ClassDef):
        prev = self.current_class
        self.current_class = node.name
        doc = ast.get_docstring(node) or ""
        self.symbols.append(SymbolInfo(
            name=node.name,
            kind="class",
            line_start=node.lineno,
            line_end=getattr(node, "end_lineno", node.lineno),
            file_path=self.file_path,
            docstring=doc,
        ))
        self.generic_visit(node)
        self.current_class = prev

    def visit_FunctionDef(self, node: ast.FunctionDef):
        self._record_func(node)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        self._record_func(node)
        self.generic_visit(node)

    def _record_func(self, node: ast.FunctionDef | ast.AsyncFunctionDef):
        kind = "method" if self.current_class else "function"
        args = [a.arg for a in node.args.args]
        sig = f"def {node.name}({', '.join(args)})"
        doc = ast.get_docstring(node) or ""
        self.symbols.append(SymbolInfo(
            name=node.name,
            kind=kind,
            line_start=node.lineno,
            line_end=getattr(node, "end_lineno", node.lineno),
            file_path=self.file_path,
            signature=sig,
            parent_class=self.current_class,
            docstring=doc,
        ))


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

        visitor = _SymbolVisitor(file_path)
        visitor.visit(tree)
        return visitor.symbols

    def find_symbol_location(self, file_path: str, symbol_name: str) -> tuple[int, int]:
        """Find the start and end line of a symbol within a file."""
        symbols = self.extract_symbols(file_path)
        clean_name = symbol_name.split(".")[-1].strip().lower()
        class_match = None
        for s in symbols:
            if s.name.lower() == clean_name or f"{s.parent_class}.{s.name}".lower() == symbol_name.lower():
                if s.kind in ("method", "function"):
                    return s.line_start, s.line_end
                elif s.kind == "class" and class_match is None:
                    class_match = (s.line_start, s.line_end)
        if class_match:
            return class_match
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

    def extract_surgical_unit(
        self,
        file_path: str,
        symbol_name: str = "",
        issue_text: str = "",
        max_unit_lines: int = 150,
        target_line: int = 0,
    ) -> tuple[str, int, int, str]:
        """Extract the smallest complete, syntactically grounded editable unit and behavior context.

        Hierarchy: function -> method -> small class region -> class -> file window

        Returns:
            (editable_source, win_start, win_end, behavior_context)
        """
        full_path = self.repo_dir / file_path
        if not full_path.exists():
            return "", 0, 0, ""

        try:
            content = full_path.read_text(encoding="utf-8", errors="replace")
            lines = content.splitlines(keepends=True)
        except Exception:
            return "", 0, 0, ""

        total_lines = len(lines)
        if not total_lines:
            return "", 0, 0, ""

        symbols = self.extract_symbols(file_path)
        if not symbols:
            win_start = 1
            win_end = min(total_lines, 100)
            return "".join(lines[win_start - 1 : win_end]), win_start, win_end, ""

        target_sym: SymbolInfo | None = None
        clean_name = symbol_name.split(".")[-1].strip() if symbol_name else ""

        if target_line > 0:
            for s in symbols:
                if s.line_start <= target_line <= s.line_end and s.kind in ("method", "function"):
                    target_sym = s
                    break
            if not target_sym:
                for s in symbols:
                    if s.line_start <= target_line <= s.line_end:
                        target_sym = s
                        break

        if not target_sym and clean_name:
            # 1. Exact case match (prefer method/function over class)
            for s in symbols:
                if (s.name == clean_name or f"{s.parent_class}.{s.name}" == symbol_name) and s.kind in ("method", "function"):
                    target_sym = s
                    break

            if not target_sym:
                for s in symbols:
                    if (s.name.lower() == clean_name.lower() or f"{s.parent_class}.{s.name}".lower() == symbol_name.lower()) and s.kind in ("method", "function"):
                        target_sym = s
                        break

            if not target_sym:
                for s in symbols:
                    if s.name == clean_name and s.kind == "class":
                        target_sym = s
                        break

            if not target_sym:
                for s in symbols:
                    if s.name.lower() == clean_name.lower() and s.kind == "class":
                        target_sym = s
                        break

        # Class-to-method narrowing if target is a class
        if target_sym and target_sym.kind == "class":
            class_methods = [s for s in symbols if s.parent_class == target_sym.name]
            narrowed_method: SymbolInfo | None = None
            if class_methods and issue_text:
                attr_calls = set(re.findall(r"\.([A-Za-z_][A-Za-z0-9_]+)", issue_text))
                words = set(re.findall(r"\b[A-Za-z_][A-Za-z0-9_]+\b", issue_text))
                for m in class_methods:
                    if m.name in attr_calls:
                        narrowed_method = m
                        break
                if not narrowed_method:
                    for m in class_methods:
                        if m.name in words and m.name not in ("__repr__", "__str__"):
                            narrowed_method = m
                            break
                if not narrowed_method:
                    init_m = next((m for m in class_methods if m.name == "__init__"), None)
                    if init_m and init_m.signature:
                        init_args = [
                            a.strip() for a in init_m.signature.replace("def __init__(", "").rstrip(")").split(",")
                            if a.strip() and a.strip() not in ("self", "cls", "*args", "**kwargs")
                        ]
                        if any(arg in words for arg in init_args) or any(k in words for k in ("init", "constructor", "create", "instantiate")):
                            narrowed_method = init_m
            if narrowed_method:
                target_sym = narrowed_method

        if not target_sym:
            funcs = [s for s in symbols if s.kind in ("method", "function")]
            target_sym = funcs[0] if funcs else symbols[0]

        behavior_context_parts: list[str] = []
        if target_sym.kind == "method":
            win_start = target_sym.line_start
            win_end = min(total_lines, target_sym.line_end)
            parent_class_info = next((s for s in symbols if s.name == target_sym.parent_class and s.kind == "class"), None)
            if parent_class_info:
                p_lines = lines[parent_class_info.line_start - 1 : min(parent_class_info.line_start + 4, total_lines)]
                p_header = "".join(p_lines).strip()
                behavior_context_parts.append(f"Parent Class: {parent_class_info.name} (Lines {parent_class_info.line_start}-{parent_class_info.line_end})\n{p_header}")
                sibling_sigs = [
                    s.signature for s in symbols
                    if s.parent_class == parent_class_info.name and s.name != target_sym.name and s.signature
                ]
                if sibling_sigs:
                    behavior_context_parts.append("Sibling Methods: " + ", ".join(sibling_sigs[:6]))

        elif target_sym.kind == "function":
            win_start = target_sym.line_start
            win_end = min(total_lines, target_sym.line_end)
            behavior_context_parts.append(f"Function: {target_sym.name} (Lines {win_start}-{win_end})")
            if target_sym.signature:
                behavior_context_parts.append(f"Signature: {target_sym.signature}")

        else:
            class_len = target_sym.line_end - target_sym.line_start + 1
            if class_len <= max_unit_lines:
                win_start = target_sym.line_start
                win_end = min(total_lines, target_sym.line_end)
            else:
                win_start = target_sym.line_start
                win_end = min(total_lines, target_sym.line_start + max_unit_lines)
            behavior_context_parts.append(f"Class Region: {target_sym.name} (Lines {win_start}-{win_end} of total {class_len})")

        editable_source = "".join(lines[win_start - 1 : win_end])
        behavior_context = "\n".join(behavior_context_parts)
        return editable_source, win_start, win_end, behavior_context
