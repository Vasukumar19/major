"""AST-grounded source reconstructor for PatchForge AI.

Takes original source text, a verified RepairUnit, and StructuredRepairOutput.
Applies surgical replacements with indentation and newline preservation,
then emits deterministic unified diffs without LLM diff generation.
"""
from __future__ import annotations

import difflib
import logging
from typing import Any, Dict, List, Optional, Tuple

from patchforge.repair.schema import (
    ReconstructedPatch,
    RepairAction,
    RepairUnit,
    RepairUnitType,
    StaticValidationResult,
    StructuredRepairOutput,
)

logger = logging.getLogger(__name__)


class SourceReconstructor:
    """Deterministically applies structured replacements into source code and builds unified diffs."""

    @staticmethod
    def reconstruct_single(
        original_code: str,
        unit: RepairUnit,
        output: StructuredRepairOutput,
        file_rel_path: str = "",
    ) -> ReconstructedPatch:
        """Applies a single RepairUnit replacement into original source code."""
        rel_path = (file_rel_path or unit.file_path).replace("\\", "/")
        lines = original_code.splitlines(keepends=True)
        total_lines = len(lines)

        start_l = max(1, min(unit.start_line, total_lines))
        end_l = max(start_l, min(unit.end_line, total_lines))

        # Detect newline style of original source
        newline = "\r\n" if "\r\n" in original_code else "\n"

        replacement_text = output.replacement.strip("\r\n")

        # Handle different granularities
        if unit.unit_type == RepairUnitType.EXPRESSION and unit.start_col is not None and unit.end_col is not None:
            # Character-level expression replacement
            try:
                new_code = SourceReconstructor._replace_expression_span(
                    original_code,
                    lines,
                    unit.start_line,
                    unit.start_col,
                    unit.end_line,
                    unit.end_col,
                    replacement_text,
                )
            except Exception as e:
                logger.warning(f"Expression span replacement failed: {e}, falling back to line replacement")
                new_code = SourceReconstructor._replace_line_span(
                    lines, start_l, end_l, unit.indentation, replacement_text, newline
                )
        elif output.repair_action == RepairAction.INSERT_STATEMENT.value:
            # Insert statement at start line
            new_code = SourceReconstructor._insert_statement(
                lines, start_l, unit.indentation, replacement_text, newline
            )
        elif output.repair_action == RepairAction.DELETE_STATEMENT.value:
            # Delete line span
            new_code = SourceReconstructor._delete_line_span(lines, start_l, end_l)
        else:
            # Statement, block, function, or method replacement
            new_code = SourceReconstructor._replace_line_span(
                lines, start_l, end_l, unit.indentation, replacement_text, newline
            )

        # Build deterministic unified diff
        orig_split = original_code.splitlines(keepends=True)
        new_split = new_code.splitlines(keepends=True)

        diff = difflib.unified_diff(
            orig_split,
            new_split,
            fromfile=f"a/{rel_path}",
            tofile=f"b/{rel_path}",
            lineterm="",
        )
        patch_text = "".join(diff)
        if patch_text and not patch_text.endswith("\n"):
            patch_text += "\n"

        success = bool(patch_text.strip())
        err = "" if success else "No changes produced between original source and reconstructed source."

        return ReconstructedPatch(
            success=success,
            files_changed=[rel_path] if success else [],
            symbols_changed=[unit.symbol] if success and unit.symbol else [],
            patch_text=patch_text,
            modified_contents={rel_path: new_code} if success else {},
            units_applied=[unit] if success else [],
            error=err,
        )

    @staticmethod
    def reconstruct_multi(
        files_source: Dict[str, str],
        edits: List[Tuple[RepairUnit, StructuredRepairOutput]],
    ) -> ReconstructedPatch:
        """Applies multiple non-overlapping RepairUnits across one or more files."""
        # Group edits by file
        by_file: Dict[str, List[Tuple[RepairUnit, StructuredRepairOutput]]] = {}
        for unit, out in edits:
            rel = unit.file_path.replace("\\", "/")
            by_file.setdefault(rel, []).append((unit, out))

        all_patches: List[str] = []
        files_changed: List[str] = []
        symbols_changed: List[str] = []
        modified_contents: Dict[str, str] = {}
        units_applied: List[RepairUnit] = []

        for rel_path, file_edits in by_file.items():
            if rel_path not in files_source:
                return ReconstructedPatch(
                    success=False,
                    error=f"Source content for file '{rel_path}' not provided.",
                )

            # Sort edits in reverse line order so line shifts don't invalidate subsequent offsets!
            file_edits.sort(key=lambda x: x[0].start_line, reverse=True)

            # Check for overlapping spans
            for i in range(len(file_edits) - 1):
                u_curr = file_edits[i][0]
                u_next = file_edits[i + 1][0]
                if u_next.end_line >= u_curr.start_line:
                    return ReconstructedPatch(
                        success=False,
                        error=f"Conflicting overlapping repair units in {rel_path}: [{u_next.start_line}-{u_next.end_line}] overlaps [{u_curr.start_line}-{u_curr.end_line}].",
                    )

            curr_source = files_source[rel_path]
            for unit, out in file_edits:
                rec = SourceReconstructor.reconstruct_single(curr_source, unit, out, rel_path)
                if not rec.success:
                    return rec
                curr_source = rec.modified_contents[rel_path]
                units_applied.append(unit)
                if unit.symbol and unit.symbol not in symbols_changed:
                    symbols_changed.append(unit.symbol)

            # Build full file unified diff
            orig_split = files_source[rel_path].splitlines(keepends=True)
            new_split = curr_source.splitlines(keepends=True)
            diff = difflib.unified_diff(
                orig_split,
                new_split,
                fromfile=f"a/{rel_path}",
                tofile=f"b/{rel_path}",
                lineterm="",
            )
            file_patch = "".join(diff)
            if file_patch.strip():
                if not file_patch.endswith("\n"):
                    file_patch += "\n"
                all_patches.append(file_patch)
                files_changed.append(rel_path)
                modified_contents[rel_path] = curr_source

        return ReconstructedPatch(
            success=bool(all_patches),
            files_changed=files_changed,
            symbols_changed=symbols_changed,
            patch_text="".join(all_patches),
            modified_contents=modified_contents,
            units_applied=units_applied,
            error="" if all_patches else "No diffs generated across multi-site edits.",
        )

    @staticmethod
    def _normalize_replacement_indentation(replacement: str, base_indent: str) -> List[str]:
        """Normalizes relative indentation of replacement lines to align with base_indent."""
        replacement = replacement.expandtabs(4)
        base_indent = base_indent.expandtabs(4)
        rep_lines = replacement.splitlines()
        non_empty = [line for line in rep_lines if line.strip()]
        if not non_empty:
            return [base_indent]

        min_indent = min(len(line) - len(line.lstrip()) for line in non_empty)
        adjusted = []
        for line in rep_lines:
            if not line.strip():
                adjusted.append("")
            else:
                rel_line = line[min_indent:] if len(line) >= min_indent else line.lstrip()
                adjusted.append(f"{base_indent}{rel_line}")
        return adjusted

    @staticmethod
    def _replace_line_span(
        lines: List[str],
        start_l: int,
        end_l: int,
        base_indent: str,
        replacement: str,
        newline: str,
    ) -> str:
        """Replaces lines start_l..end_l (1-indexed) with properly indented replacement lines."""
        adjusted_rep_lines = SourceReconstructor._normalize_replacement_indentation(replacement, base_indent)

        before = lines[: start_l - 1]
        after = lines[end_l:]

        replacement_block = [line + newline for line in adjusted_rep_lines]
        new_lines = before + replacement_block + after
        return "".join(new_lines)

    @staticmethod
    def _replace_expression_span(
        source_code: str,
        lines: List[str],
        start_line: int,
        start_col: int,
        end_line: int,
        end_col: int,
        replacement: str,
    ) -> str:
        """Replaces exact character slice within source code."""
        start_offset = sum(len(lines[i]) for i in range(start_line - 1)) + start_col
        end_offset = sum(len(lines[i]) for i in range(end_line - 1)) + end_col

        if 0 <= start_offset <= end_offset <= len(source_code):
            return source_code[:start_offset] + replacement + source_code[end_offset:]
        raise ValueError("Calculated offsets out of bounds.")

    @staticmethod
    def _insert_statement(
        lines: List[str],
        insert_line: int,
        base_indent: str,
        replacement: str,
        newline: str,
    ) -> str:
        """Inserts statement before insert_line with indentation."""
        adjusted = SourceReconstructor._normalize_replacement_indentation(replacement, base_indent)
        insert_block = [line + newline for line in adjusted]

        before = lines[: insert_line - 1]
        after = lines[insert_line - 1:]
        return "".join(before + insert_block + after)

    @staticmethod
    def _delete_line_span(lines: List[str], start_l: int, end_l: int) -> str:
        """Deletes lines start_l..end_l."""
        before = lines[: start_l - 1]
        after = lines[end_l:]
        return "".join(before + after)
