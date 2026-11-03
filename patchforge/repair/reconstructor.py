"""AST-grounded source reconstructor for PatchForge AI.

Takes original source text, a verified RepairUnit, and StructuredRepairOutput.
Applies surgical replacements with indentation and newline preservation,
then emits deterministic unified diffs without LLM diff generation.
"""
from __future__ import annotations

import ast
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

        # Auto-heal missing imports introduced by replacement snippets
        healed_imports: list[str] = []
        new_code = SourceReconstructor._auto_heal_missing_imports(original_code, new_code, record_healed=healed_imports)

        # Build deterministic unified diff
        orig_split = original_code.replace("\r\n", "\n").splitlines(keepends=True)
        new_split = new_code.replace("\r\n", "\n").splitlines(keepends=True)

        diff = difflib.unified_diff(
            orig_split,
            new_split,
            fromfile=f"a/{rel_path}",
            tofile=f"b/{rel_path}",
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
            healed_imports=healed_imports,
        )

    @staticmethod
    def reconstruct_multi(
        files_source: Optional[Dict[str, str]] = None,
        edits: Optional[List[Tuple[RepairUnit, StructuredRepairOutput]]] = None,
        original_sources: Optional[Dict[str, str]] = None,
        **kwargs,
    ) -> ReconstructedPatch:
        """Applies multiple non-overlapping RepairUnits across one or more files."""
        sources = original_sources if original_sources is not None else (files_source or {})
        edits_list = edits or []
        # Group edits by file
        by_file: Dict[str, List[Tuple[RepairUnit, StructuredRepairOutput]]] = {}
        for unit, out in edits_list:
            rel = unit.file_path.replace("\\", "/")
            by_file.setdefault(rel, []).append((unit, out))

        all_patches: List[str] = []
        files_changed: List[str] = []
        symbols_changed: List[str] = []
        modified_contents: Dict[str, str] = {}
        units_applied: List[RepairUnit] = []

        for rel_path, file_edits in by_file.items():
            if rel_path not in sources:
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

            curr_source = sources[rel_path]
            for unit, out in file_edits:
                rec = SourceReconstructor.reconstruct_single(curr_source, unit, out, rel_path)
                if not rec.success:
                    return rec
                curr_source = rec.modified_contents[rel_path]
                units_applied.append(unit)
                if unit.symbol and unit.symbol not in symbols_changed:
                    symbols_changed.append(unit.symbol)

            # Build full file unified diff
            orig_split = sources[rel_path].replace("\r\n", "\n").splitlines(keepends=True)
            new_split = curr_source.replace("\r\n", "\n").splitlines(keepends=True)
            diff = difflib.unified_diff(
                orig_split,
                new_split,
                fromfile=f"a/{rel_path}",
                tofile=f"b/{rel_path}",
            )
            file_patch = "".join(diff)
            if file_patch.strip():
                if not file_patch.endswith("\n"):
                    file_patch += "\n"
                all_patches.append(file_patch)
                files_changed.append(rel_path)
                modified_contents[rel_path] = curr_source

        # In-memory AST syntax validation across all modified Python files
        if all_patches:
            for rel_p, code in modified_contents.items():
                if rel_p.endswith(".py"):
                    try:
                        ast.parse(code, filename=rel_p)
                    except SyntaxError as syn_err:
                        return ReconstructedPatch(
                            success=False,
                            files_changed=[],
                            symbols_changed=[],
                            patch_text="",
                            modified_contents={},
                            units_applied=[],
                            error=f"AST syntax error in multi-site reconstructed code for {rel_p}: {syn_err}",
                        )

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
        import ast
        import textwrap
        replacement = replacement.expandtabs(4)
        base_indent = base_indent.expandtabs(4)
        rep_lines = replacement.splitlines()
        non_empty = [line for line in rep_lines if line.strip()]
        if not non_empty:
            return [base_indent]

        # Inconsistent indentation healing:
        # Check if replacement contains sibling def/class definitions at inconsistent indentation levels
        def_indents = [len(l) - len(l.lstrip()) for l in rep_lines if l.lstrip().startswith(('def ', 'async def ', 'class '))]
        if len(def_indents) > 1 and len(set(def_indents)) > 1:
            try:
                ast.parse(textwrap.dedent(replacement))
            except (IndentationError, SyntaxError):
                target_def_indent = min(def_indents)
                normalized_cand = []
                current_shift = 0
                for l in rep_lines:
                    if not l.strip():
                        normalized_cand.append('')
                        continue
                    stripped = l.lstrip()
                    lead = len(l) - len(stripped)
                    if stripped.startswith(('def ', 'async def ', 'class ')):
                        current_shift = lead - target_def_indent
                        normalized_cand.append(' ' * target_def_indent + stripped)
                    else:
                        new_lead = max(0, lead - current_shift)
                        normalized_cand.append(' ' * new_lead + stripped)
                healed_text = '\n'.join(normalized_cand)
                try:
                    ast.parse(textwrap.dedent(healed_text))
                    replacement = healed_text
                except Exception:
                    pass

        dedented = textwrap.dedent(replacement)
        adjusted = []
        for line in dedented.splitlines():
            if not line.strip():
                adjusted.append("")
            else:
                adjusted.append(f"{base_indent}{line}")
        return adjusted

    @staticmethod
    def _is_name_bound_in_scope(target_name: str, node: ast.AST, tree: ast.AST) -> bool:
        """Determines if target_name is locally bound (parameter, loop var, local var) in any enclosing scope."""
        import ast

        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                setattr(child, "_ast_parent", parent)

        def _get_target_names(t) -> set[str]:
            names = set()
            if isinstance(t, ast.Name):
                names.add(t.id)
            elif isinstance(t, (ast.Tuple, ast.List)):
                for elt in t.elts:
                    names.update(_get_target_names(elt))
            elif isinstance(t, ast.Starred):
                names.update(_get_target_names(t.value))
            return names

        def _get_scope_bound_names(scope_node: ast.AST) -> set[str]:
            bound = set()
            if isinstance(scope_node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                args_obj = scope_node.args
                for a in getattr(args_obj, "posonlyargs", []) + args_obj.args + getattr(args_obj, "kwonlyargs", []):
                    bound.add(a.arg)
                if args_obj.vararg:
                    bound.add(args_obj.vararg.arg)
                if args_obj.kwarg:
                    bound.add(args_obj.kwarg.arg)

            if isinstance(scope_node, ast.comprehension):
                bound.update(_get_target_names(scope_node.target))

            body = getattr(scope_node, "body", [])
            if not isinstance(body, list):
                body = [body]
            for stmt in body:
                for sub in ast.walk(stmt):
                    if sub is not stmt and isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        continue
                    if isinstance(sub, ast.Assign):
                        for tgt in sub.targets:
                            bound.update(_get_target_names(tgt))
                    elif isinstance(sub, (ast.AugAssign, ast.AnnAssign)):
                        bound.update(_get_target_names(sub.target))
                    elif isinstance(sub, (ast.For, ast.AsyncFor)):
                        bound.update(_get_target_names(sub.target))
                    elif isinstance(sub, (ast.With, ast.AsyncWith)):
                        for item in sub.items:
                            if item.optional_vars:
                                bound.update(_get_target_names(item.optional_vars))
                    elif isinstance(sub, ast.ExceptHandler):
                        if sub.name:
                            bound.add(sub.name)
                    elif isinstance(sub, ast.NamedExpr):
                        bound.update(_get_target_names(sub.target))
                    elif isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        bound.add(sub.name)
            return bound

        curr = getattr(node, "_ast_parent", None)
        while curr is not None:
            if isinstance(curr, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.comprehension, ast.Lambda)):
                if target_name in _get_scope_bound_names(curr):
                    return True
            curr = getattr(curr, "_ast_parent", None)

        return False

    @staticmethod
    def _auto_heal_missing_imports(
        original_code: str,
        modified_code: str,
        record_healed: Optional[list[str]] = None,
    ) -> str:
        """Heals missing top-level standard library and exception imports introduced by replacement snippets."""
        import ast
        import sys
        try:
            mod_tree = ast.parse(modified_code)
            orig_tree = ast.parse(original_code)
        except Exception:
            return modified_code

        def get_imported(tree):
            imported = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for a in node.names:
                        imported.add(a.asname or a.name.split('.')[0])
                elif isinstance(node, ast.ImportFrom):
                    for a in node.names:
                        imported.add(a.asname or a.name)
            return imported

        orig_imp = get_imported(orig_tree)
        mod_imp = get_imported(mod_tree)

        # 1. Standard library module attributes (e.g., socket.error without 'import socket')
        # Scope-aware: Ignore local variables, parameters, loop variables, or shadowed names.
        std_mods = set(sys.stdlib_module_names) if hasattr(sys, 'stdlib_module_names') else {
            'socket', 'sys', 'os', 'json', 're', 'collections', 'datetime', 'io', 'math'
        }
        needed_std_imports = set()
        for node in ast.walk(mod_tree):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                mod_name = node.value.id
                if mod_name in std_mods and mod_name not in mod_imp:
                    # Verify name is NOT locally bound in any enclosing scope
                    if not SourceReconstructor._is_name_bound_in_scope(mod_name, node, mod_tree):
                        needed_std_imports.add(mod_name)

        # 2. Module-level exception imports (e.g., ConnectionError from .exceptions)
        # Unconditionally ignore Python built-in identifiers
        import builtins
        builtin_names = set(dir(builtins)) | {
            "str", "bytes", "int", "float", "bool", "dict", "list", "set", "tuple",
            "object", "type", "complex", "bytearray", "memoryview", "frozenset",
            "range", "slice", "enumerate", "filter", "map", "zip", "sum", "min", "max",
            "all", "any", "id", "hash", "len", "repr", "print", "open", "super",
            "isinstance", "issubclass", "callable", "iter", "next", "classmethod",
            "staticmethod", "property", "vars", "dir", "help", "__import__",
            "Exception", "BaseException", "ValueError", "TypeError", "KeyError",
            "AttributeError", "IndexError", "LookupError", "RuntimeError",
            "StopIteration", "SyntaxError", "ImportError", "ModuleNotFoundError",
        }

        needed_exception_imports = set()
        common_exceptions = {
            'ConnectionError', 'ChunkedEncodingError', 'ContentDecodingError',
            'StreamConsumedError', 'HTTPError', 'RequestException', 'Timeout', 'TooManyRedirects'
        }
        used_names = set()
        for node in ast.walk(mod_tree):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                if node.id in common_exceptions and node.id not in mod_imp:
                    if not SourceReconstructor._is_name_bound_in_scope(node.id, node, mod_tree):
                        needed_exception_imports.add(node.id)
                elif node.id in builtin_names:
                    continue
                elif not SourceReconstructor._is_name_bound_in_scope(node.id, node, mod_tree):
                    used_names.add(node.id)

        exceptions_node = None
        for node in orig_tree.body:
            if isinstance(node, ast.ImportFrom) and node.module == 'exceptions':
                exceptions_node = node
                break
        if not exceptions_node:
            for node in orig_tree.body:
                if isinstance(node, ast.ImportFrom) and node.module and node.module.endswith('exceptions'):
                    exceptions_node = node
                    break

        # 3. Project-level compat utilities (e.g. to_native_string, builtin_str)
        # NEVER guess built-in names (str, bytes) as missing compat imports
        needed_compat_imports = set()
        compat_node = None
        for node in orig_tree.body:
            if isinstance(node, ast.ImportFrom) and node.module and 'compat' in node.module:
                compat_node = node
                break
        if compat_node:
            common_compat = {'to_native_string', 'builtin_str', 'is_py2', 'is_py3'}
            for util in common_compat:
                if util in used_names and util not in mod_imp:
                    needed_compat_imports.add(util)

        all_healed = sorted(needed_std_imports | needed_exception_imports | needed_compat_imports)
        if not all_healed:
            return modified_code

        if record_healed is not None:
            record_healed.extend(all_healed)

        lines = modified_code.splitlines(keepends=True)
        newline = "\r\n" if "\r\n" in modified_code else "\n"

        if needed_exception_imports and exceptions_node:
            end_l = getattr(exceptions_node, 'end_lineno', exceptions_node.lineno)
            mod_name = exceptions_node.module or ''
            dots = '.' * exceptions_node.level if hasattr(exceptions_node, 'level') else ''
            full_mod = f"{dots}{mod_name}"
            imp_line = f"from {full_mod} import " + ", ".join(sorted(needed_exception_imports)) + newline
            lines.insert(end_l, imp_line)

        if needed_compat_imports and compat_node:
            end_l = getattr(compat_node, 'end_lineno', compat_node.lineno)
            mod_name = compat_node.module or ''
            dots = '.' * compat_node.level if hasattr(compat_node, 'level') else ''
            full_mod = f"{dots}{mod_name}"
            imp_line = f"from {full_mod} import " + ", ".join(sorted(needed_compat_imports)) + newline
            lines.insert(end_l, imp_line)

        try:
            curr_tree = ast.parse("".join(lines))
        except Exception:
            curr_tree = mod_tree

        if needed_std_imports:
            last_imp_idx = 0
            for node in curr_tree.body:
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    end_l = getattr(node, 'end_lineno', node.lineno)
                    last_imp_idx = max(last_imp_idx, end_l)
            insert_stmts = [f"import {m}{newline}" for m in sorted(needed_std_imports)]
            if last_imp_idx > 0:
                lines = lines[:last_imp_idx] + insert_stmts + lines[last_imp_idx:]
            else:
                lines = insert_stmts + lines

        healed_code = "".join(lines)
        try:
            ast.parse(healed_code)
            return healed_code
        except Exception:
            return modified_code

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


