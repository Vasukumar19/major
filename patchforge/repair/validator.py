"""Static repair validation gate for PatchForge AI.

Performs deterministic multi-point checks prior to expensive Docker test execution:
1. Syntax validity (ast.parse)
2. Markdown / diff marker leakage
3. Intended target modification verification
4. Unrelated symbols preservation verification
5. Patch size and line expansion limits
"""
from __future__ import annotations

import ast
import difflib
import logging
from typing import Any, Dict, List, Optional, Set

from patchforge.repair.schema import (
    ReconstructedPatch,
    RepairUnit,
    StaticValidationResult,
)

logger = logging.getLogger(__name__)


class StaticRepairValidator:
    """Rigorous static validation gate running prior to Docker container test execution."""

    DISALLOWED_MARKERS = [
        "```",
        "diff --git",
        "<<<<<<< SEARCH",
        "=======",
        ">>>>>>> REPLACE",
        "@@ -",
    ]

    @classmethod
    def validate(
        cls,
        original_sources: Dict[str, str],
        reconstructed: ReconstructedPatch,
        target_units: List[RepairUnit],
        max_line_expansion: int = 150,
    ) -> StaticValidationResult:
        """Validates reconstructed source code against AST, expansion, and preservation gates."""
        res = StaticValidationResult(valid=True)
        errors: List[str] = []

        if not reconstructed.success or not reconstructed.modified_contents:
            res.valid = False
            res.errors.append("Reconstruction marked as failed or has empty modified contents.")
            return res

        total_added = 0
        total_deleted = 0
        symbols_affected: Set[str] = set()

        for file_path, new_source in reconstructed.modified_contents.items():
            orig_source = original_sources.get(file_path, "")

            # 1. Check for Accidental Markdown or Diff Leakage
            for marker in cls.DISALLOWED_MARKERS:
                if marker in new_source and marker not in orig_source:
                    res.no_markdown_leakage = False
                    errors.append(f"Markdown or diff marker '{marker}' leaked into source file {file_path}")

            # 2. Syntax & AST Validation
            try:
                new_tree = ast.parse(new_source)
                orig_tree = ast.parse(orig_source) if orig_source.strip() else None
            except SyntaxError as e:
                res.syntax_ok = False
                res.ast_ok = False
                err_snippet = f" (line content: '{e.text.strip()}')" if e.text else ""
                errors.append(f"Syntax error in reconstructed {file_path}:{e.lineno}: {e.msg}{err_snippet}")
                continue

            # 3. Verify Target Changed
            orig_lines = orig_source.splitlines()
            new_lines = new_source.splitlines()
            if orig_lines == new_lines:
                res.target_changed = False
                errors.append(f"No changes detected in modified file {file_path}")

            # 4. Line Expansion & Patch Size Gate
            diff = list(
                difflib.unified_diff(
                    orig_lines,
                    new_lines,
                    fromfile=file_path,
                    tofile=file_path,
                    lineterm="",
                )
            )
            added = sum(1 for line in diff if line.startswith("+") and not line.startswith("+++"))
            deleted = sum(1 for line in diff if line.startswith("-") and not line.startswith("---"))
            total_added += added
            total_deleted += deleted

            if added > max_line_expansion:
                res.line_expansion_ok = False
                errors.append(
                    f"Patch expansion exceeded threshold: +{added} lines added (max allowed {max_line_expansion})"
                )

            # 5. Extract affected symbols and verify preservation of unrelated symbols
            if orig_tree and new_tree:
                orig_symbols = {
                    node.name: node
                    for node in ast.walk(orig_tree)
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                }
                new_symbols = {
                    node.name: node
                    for node in ast.walk(new_tree)
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                }

                target_sym_names = {u.symbol.split(".")[-1] for u in target_units if u.symbol}

                for sym_name, orig_node in orig_symbols.items():
                    if sym_name not in target_sym_names:
                        # Check that unrelated symbol was not inadvertently removed
                        if sym_name not in new_symbols:
                            res.unrelated_targets_preserved = False
                            errors.append(
                                f"Unrelated symbol '{sym_name}' was inadvertently removed from {file_path}"
                            )
                    else:
                        symbols_affected.add(sym_name)

        res.lines_added = total_added
        res.lines_deleted = total_deleted
        res.symbols_affected = sorted(list(symbols_affected))
        res.errors = errors

        # Overall validity
        res.valid = (
            res.syntax_ok
            and res.ast_ok
            and res.target_changed
            and res.no_markdown_leakage
            and res.line_expansion_ok
            and res.unrelated_targets_preserved
        )

        return res
