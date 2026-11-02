"""File reading and patch manipulation tools for PatchForge v0.3."""
from __future__ import annotations

import difflib
import os
from pathlib import Path
from typing import Any

from patchforge.repair.generator import apply_edits_detailed, parse_edits, validate_ast
from patchforge.repair.patch import Patch
from patchforge.tools.base import Tool, ToolResult


def _resolve_and_contain(repo_path: Path, file_path: str, must_exist: bool = False) -> tuple[Path | None, str | None]:
    """Validates that file_path resolves strictly within the repository root.
    Rejects parent traversal (..), absolute paths escaping root, and symlink escapes.
    Returns (resolved_path, None) or (None, error_message).
    """
    if not file_path or not str(file_path).strip():
        return None, "PATH_SECURITY_ERROR: File path cannot be empty."

    clean_p = str(file_path).replace("\\", "/").strip()
    parts = clean_p.split("/")
    if ".." in parts:
        return None, f"PATH_SECURITY_ERROR: Directory traversal rejected for path: '{file_path}'"

    repo_resolved = repo_path.resolve()
    candidate = Path(file_path)

    if candidate.is_absolute():
        try:
            cand_resolved = candidate.resolve()
            if not cand_resolved.is_relative_to(repo_resolved):
                return None, f"PATH_SECURITY_ERROR: Absolute path '{file_path}' escapes repository root '{repo_resolved}'"
            full_path = cand_resolved
        except (ValueError, RuntimeError):
            return None, f"PATH_SECURITY_ERROR: Absolute path '{file_path}' escapes repository root"
    else:
        full_path = (repo_resolved / clean_p).resolve()
        try:
            if not full_path.is_relative_to(repo_resolved):
                return None, f"PATH_SECURITY_ERROR: Resolved path '{full_path}' escapes repository root '{repo_resolved}'"
        except (ValueError, RuntimeError):
            return None, f"PATH_SECURITY_ERROR: Resolved path escapes repository root"

    if full_path.exists():
        try:
            real_path = Path(os.path.realpath(full_path))
            real_repo = Path(os.path.realpath(repo_resolved))
            if not real_path.is_relative_to(real_repo):
                return None, f"PATH_SECURITY_ERROR: Symlink '{file_path}' resolves outside repository root: '{real_path}'"
        except (ValueError, RuntimeError):
            return None, f"PATH_SECURITY_ERROR: Symlink resolves outside repository root"

    if must_exist and not full_path.exists():
        return None, f"File '{file_path}' does not exist."

    return full_path, None


class ReadFileTool(Tool):
    name = "read_file"
    description = (
        "Read contents of a file within specified line ranges (default first 100 lines). "
        "Includes line numbers."
    )
    parameters = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "Relative path to file in the repo"},
            "start_line": {"type": "integer", "description": "Start line number (1-indexed)"},
            "end_line": {"type": "integer", "description": "End line number (1-indexed, max 150 lines per call)"},
        },
        "required": ["file_path"],
    }

    def __init__(self, repo_dir: str = ""):
        self.repo_dir = repo_dir

    def execute(self, args: dict[str, Any], state: Any = None) -> ToolResult:
        file_path = args.get("file_path", "")
        start_line = max(1, args.get("start_line", 1))
        end_line = args.get("end_line", start_line + 99)
        if end_line - start_line > 150:
            end_line = start_line + 150

        repo_path = Path(self.repo_dir or (state.repo_dir if state and hasattr(state, "repo_dir") else ".")).resolve()
        full_path, err = _resolve_and_contain(repo_path, file_path, must_exist=True)
        if err or not full_path:
            return ToolResult(self.name, status="ERROR", error=err or f"File '{file_path}' does not exist.")

        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except Exception as e:
            return ToolResult(self.name, status="ERROR", error=f"Failed to read file '{file_path}': {str(e)}")

        total_lines = len(lines)
        actual_end = min(end_line, total_lines)
        if start_line > total_lines:
            return ToolResult(
                self.name,
                status="SUCCESS",
                data={"file_path": file_path, "lines": [], "total_lines": total_lines},
                message=f"Start line {start_line} is beyond file length ({total_lines} lines).",
            )

        numbered_lines = [
            f"{i}: {lines[i - 1].rstrip()}"
            for i in range(start_line, actual_end + 1)
        ]
        content_snippet = "\n".join(numbered_lines)

        return ToolResult(
            tool_name=self.name,
            status="SUCCESS",
            data={
                "file_path": file_path,
                "start_line": start_line,
                "end_line": actual_end,
                "total_lines": total_lines,
                "content": content_snippet,
            },
            message=f"Read lines {start_line}-{actual_end} of {file_path} (total: {total_lines} lines).",
            raw_output=content_snippet,
        )


class ReadTestTool(Tool):
    name = "read_test"
    description = (
        "Read specific test methods or test files associated with the issue or reproduction."
    )
    parameters = {
        "type": "object",
        "properties": {
            "test_path": {"type": "string", "description": "Test file path or test identifier"},
            "test_name": {"type": "string", "description": "Optional specific test function or class name"},
        },
        "required": ["test_path"],
    }

    def __init__(self, repo_dir: str = ""):
        self.repo_dir = repo_dir

    def execute(self, args: dict[str, Any], state: Any = None) -> ToolResult:
        test_path = args.get("test_path", "")
        test_name = args.get("test_name", "")
        repo_path = Path(self.repo_dir or (state.repo_dir if state and hasattr(state, "repo_dir") else ".")).resolve()

        full_path, err = _resolve_and_contain(repo_path, test_path, must_exist=False)
        if err or not full_path:
            return ToolResult(self.name, status="ERROR", error=err or f"PATH_SECURITY_ERROR: Cannot access test '{test_path}'")

        if not full_path.exists():
            # Try searching in tests/ directory within repo_path only
            candidates = list(repo_path.glob(f"**/{test_path}*"))
            if candidates:
                cand = candidates[0].resolve()
                if not cand.is_relative_to(repo_path):
                    return ToolResult(self.name, status="ERROR", error=f"PATH_SECURITY_ERROR: Candidate '{cand}' escapes repository root.")
                full_path = cand
                test_path = str(full_path.relative_to(repo_path)).replace("\\", "/")
            else:
                return ToolResult(self.name, status="ERROR", error=f"Test path '{test_path}' not found.")

        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception as e:
            return ToolResult(self.name, status="ERROR", error=f"Failed to read test '{test_path}': {str(e)}")

        lines = content.splitlines()
        if test_name:
            matching_lines = []
            capturing = False
            for i, line in enumerate(lines, 1):
                if f"def {test_name}" in line or f"class {test_name}" in line:
                    capturing = True
                if capturing:
                    matching_lines.append(f"{i}: {line}")
                    if len(matching_lines) >= 80:
                        break
            if matching_lines:
                snippet = "\n".join(matching_lines)
                return ToolResult(
                    self.name,
                    status="SUCCESS",
                    data={"test_path": test_path, "test_name": test_name, "content": snippet},
                    message=f"Found test {test_name} in {test_path}.",
                    raw_output=snippet,
                )

        snippet = "\n".join(f"{i+1}: {l}" for i, l in enumerate(lines[:100]))
        return ToolResult(
            self.name,
            status="SUCCESS",
            data={"test_path": test_path, "content": snippet},
            message=f"Read first 100 lines of {test_path}.",
            raw_output=snippet,
        )


class ApplyPatchTool(Tool):
    name = "apply_patch"
    description = (
        "Apply SEARCH/REPLACE block edits to a file and validate AST syntax. "
        "Returns synthesized unified diff and validation status."
    )
    parameters = {
        "type": "object",
        "properties": {
            "patch_text": {
                "type": "string",
                "description": (
                    "SEARCH/REPLACE formatted text with file header (### path/to/file.py\\n"
                    "<<<<<<< SEARCH\\n    return value\\n=======\\n    return value + 1\\n>>>>>>> REPLACE). "
                    "SEARCH must exactly quote complete lines from the most recently read file content; "
                    "do not use placeholders or paraphrases."
                ),
            },
            "hypothesis_id": {"type": "string", "description": "Associated hypothesis ID"},
        },
        "required": ["patch_text"],
    }

    def __init__(self, repo_dir: str = "", write_to_disk: bool = True):
        self.repo_dir = repo_dir
        self.write_to_disk = write_to_disk

    def execute(self, args: dict[str, Any], state: Any = None) -> ToolResult:
        import re
        patch_text = args.get("patch_text", "")
        hypothesis_id = args.get("hypothesis_id", "H1")
        repo_path = Path(self.repo_dir or (state.repo_dir if state and hasattr(state, "repo_dir") else ".")).resolve()

        edits = parse_edits(patch_text)
        if not edits:
            return ToolResult(
                self.name,
                status="ERROR",
                error="Could not parse any valid SEARCH/REPLACE blocks. Make sure to format as '### filename\\n<<<<<<< SEARCH\\nexact original lines\\n=======\\nnew replacement lines\\n>>>>>>> REPLACE'.",
            )

        allowed_files = args.get("allowed_files")
        if not allowed_files and args.get("expected_target_file"):
            allowed_files = [args.get("expected_target_file")]

        norm_allowed = {f.replace("\\", "/").lstrip("/") for f in allowed_files} if allowed_files else None

        # PHASE 1: Target path resolution, path containment, and allowlist validation
        validated_targets: dict[str, tuple[Path, list[tuple[str, str]]]] = {}
        for rel_path, blocks in edits.items():
            full, sec_err = _resolve_and_contain(repo_path, rel_path, must_exist=False)
            if sec_err or not full:
                return ToolResult(self.name, status="ERROR", error=sec_err or f"PATH_SECURITY_ERROR for '{rel_path}'")

            if not full.exists():
                # Target grounding: check if file exists elsewhere within repo_path
                candidates = list(repo_path.glob(f"**/{Path(rel_path).name}"))
                if not candidates:
                    s_first = blocks[0][0].strip() if blocks and blocks[0] else ""
                    if s_first:
                        for py_f in repo_path.glob("**/*.py"):
                            if not any(part in ("tests", "docs", ".git", "venv") for part in py_f.parts):
                                try:
                                    if s_first in py_f.read_text(encoding="utf-8", errors="replace"):
                                        candidates.append(py_f)
                                        break
                                except Exception:
                                    pass
                if candidates:
                    full = candidates[0].resolve()
                    if not full.is_relative_to(repo_path):
                        return ToolResult(self.name, status="ERROR", error=f"PATH_SECURITY_ERROR: Candidate '{full}' escapes repository root.")
                    rel_path = str(full.relative_to(repo_path)).replace("\\", "/")
                else:
                    return ToolResult(self.name, status="ERROR", error=f"TARGET_MISMATCH: target file '{rel_path}' does not exist.")

            # Validate allowed_files after destination is resolved
            if norm_allowed is not None:
                norm_rel = rel_path.replace("\\", "/").lstrip("/")
                if norm_rel not in norm_allowed:
                    return ToolResult(
                        self.name,
                        status="ERROR",
                        error=f"TARGET_MISMATCH: target file '{rel_path}' is outside allowed targets ({', '.join(sorted(norm_allowed))}).",
                    )

            validated_targets[rel_path] = (full, blocks)

        # PHASE 2: In-memory simulation and AST validation (ALL files must pass before ANY write)
        diff_chunks = []
        files_changed = []
        new_contents = {}
        planned_writes: dict[Path, tuple[str, str, str]] = {}  # full -> (rel_path, orig, new_c)
        highest_tier = "EXACT"

        for rel_path, (full, blocks) in validated_targets.items():
            try:
                with open(full, "r", encoding="utf-8", errors="replace") as f:
                    orig = f.read()
            except Exception as e:
                return ToolResult(self.name, status="ERROR", error=f"Failed to read '{rel_path}': {str(e)}")

            new_c, tier, err = apply_edits_detailed(orig, blocks)
            if err:
                is_ws_mismatch = False
                for search_b, _ in blocks:
                    clean_s = re.sub(r"\s+", "", search_b)
                    clean_o = re.sub(r"\s+", "", orig)
                    if clean_s and clean_s in clean_o:
                        is_ws_mismatch = True
                        break

                if is_ws_mismatch:
                    return ToolResult(
                        self.name,
                        status="ERROR",
                        error=(
                            f"SEARCH_WHITESPACE_MISMATCH: The SEARCH block matches content in '{rel_path}' "
                            f"except for leading/trailing whitespace or indentation. You MUST copy lines "
                            f"exactly verbatim from EDITABLE SOURCE — VERBATIM."
                        ),
                    )
                elif "ambiguous" in err.lower():
                    return ToolResult(
                        self.name,
                        status="ERROR",
                        error=f"SEARCH_AMBIGUOUS: SEARCH block matches multiple locations in '{rel_path}'. Provide more surrounding lines.",
                    )
                else:
                    return ToolResult(
                        self.name,
                        status="ERROR",
                        error=f"SEARCH_NOT_FOUND: SEARCH block not found in '{rel_path}'. Verify lines against EDITABLE SOURCE — VERBATIM: {err}",
                    )

            # AST validation gate
            valid_ast, ast_err = validate_ast(new_c, rel_path)
            if not valid_ast:
                return ToolResult(
                    self.name,
                    status="ERROR",
                    error=f"AST Syntax validation failed on {rel_path}: {ast_err}",
                )

            diff = list(difflib.unified_diff(
                orig.splitlines(keepends=True),
                new_c.splitlines(keepends=True),
                fromfile=f"a/{rel_path}",
                tofile=f"b/{rel_path}",
            ))
            diff_chunks.append("".join(diff))
            files_changed.append(rel_path)
            new_contents[rel_path] = new_c
            planned_writes[full] = (rel_path, orig, new_c)

        # PHASE 3: Transactional atomic writes to disk with automatic rollback on error
        if args.get("write_to_disk", self.write_to_disk):
            written_backups: dict[Path, str] = {}
            try:
                for full, (rel_path, orig, new_c) in planned_writes.items():
                    # Revalidate containment immediately before writing
                    if not full.resolve().is_relative_to(repo_path):
                        raise RuntimeError(f"Path escape detected during write phase for '{full}'")
                    written_backups[full] = orig
                    with open(full, "w", encoding="utf-8") as f:
                        f.write(new_c)
            except Exception as write_err:
                # Rollback all already-written files
                rollback_errors = []
                for b_full, b_orig in written_backups.items():
                    try:
                        with open(b_full, "w", encoding="utf-8") as f:
                            f.write(b_orig)
                    except Exception as rb_err:
                        rollback_errors.append(f"{b_full}: {rb_err}")
                rb_msg = f" (Rollback errors: {'; '.join(rollback_errors)})" if rollback_errors else " (Successfully rolled back all modified files)."
                return ToolResult(
                    self.name,
                    status="ERROR",
                    error=f"TRANSACTION_WRITE_FAILED: Failed to write changes to disk: {write_err}{rb_msg}",
                )

        full_diff = "\n".join(diff_chunks)
        patch_obj = Patch(
            hypothesis_id=hypothesis_id,
            files_changed=files_changed,
            patch_text=full_diff,
            valid=True,
            match_tier=highest_tier,
            new_contents=new_contents,
        )

        return ToolResult(
            tool_name=self.name,
            status="SUCCESS",
            data={
                "patch": patch_obj.to_dict(),
                "diff": full_diff,
                "files_changed": files_changed,
                "match_tier": highest_tier,
            },
            message=f"Successfully applied patch to {len(files_changed)} files ({', '.join(files_changed)}) using {highest_tier} matching.",
            raw_output=full_diff,
        )


class GitDiffTool(Tool):
    name = "git_diff"
    description = "View the current candidate diff or working patch text."
    parameters = {
        "type": "object",
        "properties": {},
    }

    def execute(self, args: dict[str, Any], state: Any = None) -> ToolResult:
        if not state or not hasattr(state, "active_patch") or not state.active_patch:
            return ToolResult(
                self.name,
                status="SUCCESS",
                data={"diff": ""},
                message="No active patch applied in current state.",
            )
        diff = state.active_patch.patch_text if hasattr(state.active_patch, "patch_text") else str(state.active_patch)
        return ToolResult(
            self.name,
            status="SUCCESS",
            data={"diff": diff},
            message="Active patch diff retrieved.",
            raw_output=diff,
        )
