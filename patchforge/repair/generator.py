"""Generate a minimal patch FROM a selected hypothesis.

Format reuses the Agentless SEARCH/REPLACE convention (proven in Phase B),
applied in-memory and emitted as a unified diff. The repo checkout is
never mutated; evaluation happens from the diff text.
"""
from __future__ import annotations

import ast
import difflib
import os
import re
import textwrap
from dataclasses import dataclass
from enum import Enum

from patchforge.issue.problem import Problem
from patchforge.models.provider import ModelProvider
from patchforge.reasoning.hypothesis import Hypothesis
from patchforge.repair.patch import Patch
from patchforge.retrieval.retriever import Retriever

SYSTEM = (
    "You are a careful repair assistant. Produce the SMALLEST patch that "
    "addresses the hypothesis. Reply with SEARCH/REPLACE blocks only, plus "
    "one short explanation paragraph. Never use '...' or ellipsis placeholders "
    "in code; provide complete, syntactically valid Python code."
)

_EDIT_RE = re.compile(
    r"<<<<<<< SEARCH\n(?P<search>.*?)\n=======\n(?P<replace>.*?)\n>>>>>>> REPLACE",
    re.DOTALL)


class MatchStatus(str, Enum):
    EXACT = "EXACT"
    WHITESPACE_NORMALIZED = "WHITESPACE_NORMALIZED"
    INDENTATION_NORMALIZED = "INDENTATION_NORMALIZED"
    ANCHORED_GAP = "ANCHORED_GAP"
    AMBIGUOUS = "AMBIGUOUS"
    NOT_FOUND = "NOT_FOUND"


def parse_edits(text: str) -> dict[str, list[tuple[str, str]]]:
    """{file: [(search, replace), ...]}. Empty dict if unparseable.

    File headers tolerate `### path` and `### path :: symbol` forms.
    """
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        if line.strip().startswith("### "):
            current = line.strip()[4:].split("::")[0].strip().strip("`")
            sections.setdefault(current, [])
        elif current is not None:
            sections[current].append(line)
    edits: dict[str, list[tuple[str, str]]] = {}
    for file, body_lines in sections.items():
        for edit in _EDIT_RE.finditer("\n".join(body_lines)):
            edits.setdefault(file, []).append(
                (edit.group("search"), edit.group("replace")))
    return edits


def diff_to_search_replace(diff_text: str, default_file: str = "") -> str:
    """Converts a unified diff block into SEARCH/REPLACE block format."""
    lines = diff_text.splitlines()
    file_path = default_file
    for l in lines:
        if l.startswith("+++ b/"):
            file_path = l[6:].strip()
            break
        elif l.startswith("+++ "):
            file_path = l[4:].strip().lstrip("b/")
            break

    blocks: list[tuple[str, str]] = []
    curr_search: list[str] = []
    curr_replace: list[str] = []
    in_hunk = False

    for l in lines:
        if l.startswith("@@"):
            if curr_search or curr_replace:
                blocks.append(("\n".join(curr_search), "\n".join(curr_replace)))
                curr_search = []
                curr_replace = []
            in_hunk = True
            continue
        if not in_hunk:
            continue
        if l.startswith("-"):
            curr_search.append(l[1:])
        elif l.startswith("+"):
            curr_replace.append(l[1:])
        elif l.startswith(" "):
            curr_search.append(l[1:])
            curr_replace.append(l[1:])
        elif l == "":
            curr_search.append("")
            curr_replace.append("")

    if curr_search or curr_replace:
        blocks.append(("\n".join(curr_search), "\n".join(curr_replace)))

    if not blocks:
        return ""

    out = []
    if file_path:
        out.append(f"### {file_path}")
    for s, r in blocks:
        out.append("<<<<<<< SEARCH")
        out.append(s)
        out.append("=======")
        out.append(r)
        out.append(">>>>>>> REPLACE")
    return "\n".join(out)


def code_to_search_replace(code_cand: str, target: object, default_file: str = "") -> str:
    """Converts a replacement code snippet or full-function replacement into SEARCH/REPLACE format
    by diffing against verified target sites.
    """
    if not code_cand or not code_cand.strip():
        return ""
    cand_str = code_cand.strip("\r\n")
    if "<<<<<<< SEARCH" in cand_str:
        return cand_str
    if cand_str.startswith("{") or cand_str.startswith("```json") or '"action":' in cand_str:
        return ""

    sites = getattr(target, "all_sites", lambda: [])()
    if not sites:
        return ""

    def _extract_sym(src: str) -> str | None:
        try:
            tree = ast.parse(src)
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    return node.name
        except Exception:
            pass
        return None

    cand_sym = _extract_sym(cand_str)

    for site in sites:
        target_f = site.file_path or default_file
        if default_file and os.path.basename(site.file_path) != os.path.basename(default_file):
            continue
        if not site.verified_source:
            continue

        sym_match = bool(cand_sym and cand_sym == site.symbol)
        sim = difflib.SequenceMatcher(None, site.verified_source, cand_str).quick_ratio()

        if sym_match or sim >= 0.35:
            # Indentation alignment: align cand_str indentation to site.verified_source
            orig_first_nonempty = next((l for l in site.verified_source.splitlines() if l.strip()), "")
            cand_first_nonempty = next((l for l in cand_str.splitlines() if l.strip()), "")
            orig_indent = len(orig_first_nonempty) - len(orig_first_nonempty.lstrip(" "))
            cand_indent = len(cand_first_nonempty) - len(cand_first_nonempty.lstrip(" "))

            aligned_cand = cand_str
            if orig_indent > cand_indent:
                aligned_cand = textwrap.indent(cand_str, " " * (orig_indent - cand_indent))
            elif cand_indent > orig_indent:
                aligned_cand = textwrap.dedent(cand_str)
                if orig_indent > 0:
                    aligned_cand = textwrap.indent(aligned_cand, " " * orig_indent)

            orig_lines = site.verified_source.splitlines(keepends=True)
            cand_lines = [l + "\n" for l in aligned_cand.splitlines()]
            diff_lines = list(difflib.unified_diff(
                orig_lines,
                cand_lines,
                fromfile="a/" + target_f,
                tofile="b/" + target_f,
                n=3,
            ))
            if diff_lines:
                diff_str = "".join(diff_lines)
                converted = diff_to_search_replace(diff_str, target_f)
                if converted:
                    return converted
                return f"### {target_f}\n<<<<<<< SEARCH\n{site.verified_source}\n=======\n{aligned_cand}\n>>>>>>> REPLACE"
    return ""


def _denumber(text: str) -> str:
    """Strip leading N: line-number prefixes from retriever excerpts."""
    return "\n".join(re.sub(r"^\d+:", "", line) for line in text.splitlines())


def build_prompt(problem: Problem, hypothesis: Hypothesis, code: list[str],
                 previous_error: str = "") -> str:
    lines = [
        "ISSUE:",
        problem.problem_statement.strip()[:2500],
        "",
        "HYPOTHESIS " + hypothesis.id + ": " + hypothesis.description,
        "EXPECTED BEHAVIOR: " + hypothesis.expected_behavior,
        "",
        "SUPPORTING EVIDENCE:",
    ]
    for e in hypothesis.supporting_evidence[:8]:
        lines.append(f"- {e.file} :: {e.symbol} [{e.type}] {e.explanation}")
    lines += ["", "RELEVANT CODE:"]
    lines.extend(c[:2500] for c in code[:3])
    lines += ["", "FAILING TESTS: " + ", ".join(problem.fail_to_pass[:8])]
    if previous_error:
        lines += ["", "PREVIOUS ATTEMPT FAILED: " + previous_error[:800],
                  "Avoid repeating the same mistake. Address this specific failure."]
    lines += ["",
              "Emit edits as SEARCH/REPLACE blocks, one per change:",
              "```python",
              "### path/to/file.py",
              "<<<<<<< SEARCH",
              "<exact original lines>",
              "=======",
              "<replacement lines>",
              ">>>>>>> REPLACE",
              "```",
              "CRITICAL: Do NOT use '...' placeholders. Output complete, compilable python code.",
              "Then one short paragraph explaining the fix."]
    return "\n".join(lines)


def _shift_replace_indentation(rep_raw_lines: list[str], orig_indent: str) -> list[str]:
    """Shifts replacement block lines relative to original line indentation."""
    rep_non_empty = [l for l in rep_raw_lines if l.strip()]
    if not rep_non_empty:
        return [l + "\n" for l in rep_raw_lines]
    rep_base_indent = rep_non_empty[0][:len(rep_non_empty[0]) - len(rep_non_empty[0].lstrip())]
    base_len = len(rep_base_indent)

    shifted = []
    for l in rep_raw_lines:
        if not l.strip():
            shifted.append("\n")
            continue
        l_indent_len = len(l) - len(l.lstrip())
        rel_offset = l_indent_len - base_len
        if rel_offset >= 0:
            shifted.append(orig_indent + (" " * rel_offset) + l.lstrip() + "\n")
        else:
            unindent = max(0, len(orig_indent) + rel_offset)
            shifted.append((" " * unindent) + l.lstrip() + "\n")
    return shifted


def _apply_single_edit(content: str, search: str, replace: str) -> tuple[str, MatchStatus, str]:
    """Applies a single search/replace edit using ambiguity-aware 3-tier matching."""
    # Tier 1: Exact match
    if search in content:
        count = content.count(search)
        idx = content.find(search)
        line_start = content.rfind("\n", 0, idx) + 1
        leading_prefix = content[line_start:idx]

        # If search matched with leading whitespace before it on the line AND
        # either search or replace contains newlines, the model omitted or under-indented
        # a multi-line edit. Fall through to indentation-normalized matching to avoid
        # prepending unreplaced indentation to line 1 and breaking syntax.
        # For single-line replacements without newlines, exact substring replacement is safe.
        is_multiline_indent_mismatch = (
            leading_prefix
            and leading_prefix.isspace()
            and ("\n" in replace or "\n" in search)
        )

        if not is_multiline_indent_mismatch:
            if count == 1:
                return content.replace(search, replace, 1), MatchStatus.EXACT, ""
            elif count > 1:
                if "\n" not in search.strip():
                    return content, MatchStatus.AMBIGUOUS, f"exact SEARCH block matches {count} locations (ambiguous)"
                return content.replace(search, replace, 1), MatchStatus.EXACT, ""

    # Split into lines (preserving line endings)
    c_lines = content.splitlines(keepends=True)
    s_lines = search.splitlines(keepends=True)
    if not s_lines or not c_lines:
        return content, MatchStatus.NOT_FOUND, "empty search or content"

    # Diff marker cleaning pre-check
    has_diff_markers = any(l.startswith(("+", "-")) for l in s_lines)
    if has_diff_markers:
        # Standard diff has '+' or '-' prepended directly to the line: line[1:]
        clean_s_lines = [l[1:] if l.startswith(("+", "-")) else l for l in s_lines]
        clean_search = "".join(clean_s_lines)
        if clean_search in content and content.count(clean_search) == 1:
            return content.replace(clean_search, replace, 1), MatchStatus.EXACT, ""
        # Also try stripping '+ ' / '- ' if a space was added after the marker
        clean_s_lines_sp = [l[2:] if l.startswith(("+ ", "- ")) else (l[1:] if l.startswith(("+", "-")) else l) for l in s_lines]
        clean_search_sp = "".join(clean_s_lines_sp)
        if clean_search_sp in content and content.count(clean_search_sp) == 1:
            return content.replace(clean_search_sp, replace, 1), MatchStatus.EXACT, ""

    # Tier 2: Whitespace / line-ending normalized match
    s_rstripped = [l.rstrip() for l in search.splitlines()]
    c_rstripped = [l.rstrip() for l in content.splitlines()]
    n = len(s_rstripped)
    ws_matches = [i for i in range(len(c_rstripped) - n + 1) if c_rstripped[i:i+n] == s_rstripped]
    if len(ws_matches) == 1:
        idx = ws_matches[0]
        # Replace the matched slice of lines in c_lines
        rep_lines = replace.splitlines(keepends=True)
        if rep_lines and not rep_lines[-1].endswith("\n") and c_lines[idx+n-1].endswith("\n"):
            rep_lines[-1] = rep_lines[-1] + "\n"
        new_lines = c_lines[:idx] + rep_lines + c_lines[idx+n:]
        return "".join(new_lines), MatchStatus.WHITESPACE_NORMALIZED, ""
    elif len(ws_matches) > 1:
        return content, MatchStatus.AMBIGUOUS, f"whitespace-normalized SEARCH matches {len(ws_matches)} locations"

    # Tier 3: Indentation normalized match
    s_stripped = [l.strip() for l in search.splitlines()]
    c_stripped = [l.strip() for l in content.splitlines()]
    # Only match if there are non-empty search lines
    if any(s_stripped):
        indent_matches = [i for i in range(len(c_stripped) - n + 1) if c_stripped[i:i+n] == s_stripped]
        if len(indent_matches) == 1:
            idx = indent_matches[0]
            orig_first = c_lines[idx]
            orig_indent = orig_first[:len(orig_first) - len(orig_first.lstrip())]

            rep_raw_lines = replace.splitlines()
            shifted_rep = _shift_replace_indentation(rep_raw_lines, orig_indent)
            new_lines = c_lines[:idx] + shifted_rep + c_lines[idx+n:]
            return "".join(new_lines), MatchStatus.INDENTATION_NORMALIZED, ""
        elif len(indent_matches) > 1:
            return content, MatchStatus.AMBIGUOUS, f"indentation-normalized SEARCH matches {len(indent_matches)} locations"

    # Tier 4: Anchored Structural Gap Matcher
    # Handles models omitting intermediate non-executable lines (docstrings, comments, blank lines)
    # between top and bottom anchor lines of a multi-line SEARCH block.
    # Strictly rejects gaps containing executable code (assignments, calls, definitions, returns).
    s_non_empty = [(i, l.strip()) for i, l in enumerate(s_lines) if l.strip()]
    if len(s_non_empty) >= 2:
        first_s_idx, first_s = s_non_empty[0]
        last_s_idx, last_s = s_non_empty[-1]

        def _is_gap_strictly_non_executable(gap_raw_lines: list[str]) -> bool:
            in_triple_double = False
            in_triple_single = False
            for line in gap_raw_lines:
                stripped = line.strip()
                if not stripped:
                    continue
                if stripped.startswith("#"):
                    continue
                # Check triple-quote boundaries
                td_count = stripped.count('"""')
                ts_count = stripped.count("'''")
                if in_triple_double:
                    if td_count > 0:
                        in_triple_double = False
                    continue
                if in_triple_single:
                    if ts_count > 0:
                        in_triple_single = False
                    continue
                if td_count % 2 == 1:
                    in_triple_double = True
                    continue
                if ts_count % 2 == 1:
                    in_triple_single = True
                    continue
                if td_count > 0 or ts_count > 0:
                    continue
                # Line is non-empty, not a comment, and outside docstring -> executable code!
                return False
            return not (in_triple_double or in_triple_single)

        candidate_gap_matches = []
        for c_start in range(len(c_lines)):
            if c_lines[c_start].strip() != first_s:
                continue
            # Search forward for matching end anchor within a reasonable window (up to 40 lines)
            for c_end in range(c_start + 1, min(len(c_lines), c_start + 45)):
                if c_lines[c_end].strip() == last_s:
                    # Found candidate span c_lines[c_start : c_end + 1]
                    # Verify that any intermediate search lines also match in sequence
                    inter_s = [item[1] for item in s_non_empty[1:-1]]
                    inter_c = [c_lines[j].strip() for j in range(c_start + 1, c_end)]
                    # Check if all intermediate search lines appear in order
                    curr_pos = 0
                    all_found = True
                    for exp in inter_s:
                        found_idx = -1
                        for k in range(curr_pos, len(inter_c)):
                            if inter_c[k] == exp:
                                found_idx = k
                                break
                        if found_idx == -1:
                            all_found = False
                            break
                        curr_pos = found_idx + 1

                    if all_found:
                        # Inspect all lines in c_lines that were skipped by s_lines
                        matched_c_indices = {c_start, c_end}
                        # Find indices of matched intermediate lines
                        c_curr = c_start + 1
                        for exp in inter_s:
                            while c_curr < c_end and c_lines[c_curr].strip() != exp:
                                c_curr += 1
                            if c_curr < c_end:
                                matched_c_indices.add(c_curr)
                                c_curr += 1

                        gap_lines = [c_lines[j] for j in range(c_start, c_end + 1) if j not in matched_c_indices]
                        if _is_gap_strictly_non_executable(gap_lines):
                            candidate_gap_matches.append((c_start, c_end))

        if len(candidate_gap_matches) == 1:
            m_start, m_end = candidate_gap_matches[0]
            orig_first = c_lines[m_start]
            orig_indent = orig_first[:len(orig_first) - len(orig_first.lstrip())]
            rep_raw_lines = replace.splitlines()
            shifted_rep = _shift_replace_indentation(rep_raw_lines, orig_indent)
            new_lines = c_lines[:m_start] + shifted_rep + c_lines[m_end + 1:]
            return "".join(new_lines), MatchStatus.ANCHORED_GAP, ""
        elif len(candidate_gap_matches) > 1:
            return content, MatchStatus.AMBIGUOUS, f"anchored structural gap SEARCH matches {len(candidate_gap_matches)} locations"

    if has_diff_markers:
        return content, MatchStatus.NOT_FOUND, "SEARCH block contains git diff markers ('+' or '-'). The repository on disk is clean; quote lines verbatim from VERIFIED EDITABLE SOURCE."
    return content, MatchStatus.NOT_FOUND, "SEARCH block not found verbatim or normalized"


def apply_edits_detailed(original: str, edits: list[tuple[str, str]]) -> tuple[str, str, str]:
    """Returns (new_content, highest_match_tier, error). First failing block aborts."""
    content = original
    highest_tier = MatchStatus.EXACT.value
    tier_order = [
        MatchStatus.EXACT.value,
        MatchStatus.WHITESPACE_NORMALIZED.value,
        MatchStatus.INDENTATION_NORMALIZED.value,
        MatchStatus.ANCHORED_GAP.value,
    ]

    for i, (search, replace) in enumerate(edits):
        new_content, status, err = _apply_single_edit(content, search, replace)
        if status in (MatchStatus.NOT_FOUND, MatchStatus.AMBIGUOUS):
            return content, status.value, f"SEARCH block {i} error: {err}"
        content = new_content
        if tier_order.index(status.value) > tier_order.index(highest_tier):
            highest_tier = status.value

    return content, highest_tier, ""


def apply_edits(original: str, edits: list[tuple[str, str]]) -> tuple[str, str]:
    """Backward-compatible wrapper: returns (new_content, error)."""
    new, _, err = apply_edits_detailed(original, edits)
    return new, err


def validate_ast(content: str, filename: str) -> tuple[bool, str]:
    """Validate Python AST syntax and reject invalid placeholder syntax."""
    if not filename.endswith(".py"):
        return True, ""
    try:
        tree = ast.parse(content, filename=filename)
        return True, ""
    except SyntaxError as e:
        return False, f"{filename}:{e.lineno}: {e.msg}"


@dataclass
class PatchGenerator:
    provider: ModelProvider
    retriever: Retriever
    max_format_retries: int = 1
    max_syntax_retries: int = 1

    def code_context(self, hypothesis: Hypothesis) -> list[str]:
        excerpts = []
        for f in hypothesis.affected_files[:2]:
            found_symbol = False
            symbols = [s for s in hypothesis.affected_symbols if s]
            if symbols:
                for s in symbols[:2]:
                    ctx = self.retriever.symbol_context(f, s)
                    if ctx is not None and ctx.text:
                        excerpts.append(f"### {f} :: {s}\n" + _denumber(ctx.text)[:2500])
                        found_symbol = True
            if not found_symbol:
                text = self.retriever.read_file(f, max_lines=150)
                if text:
                    excerpts.append(f"### {f}\n" + _denumber(text)[:2500])
        return excerpts

    def generate(self, problem: Problem, hypothesis: Hypothesis,
                 previous_error: str = "") -> Patch:
        prompt = build_prompt(problem, hypothesis,
                              self.code_context(hypothesis), previous_error)
        gen = self.provider.generate_one(
            prompt, system=SYSTEM, max_tokens=1500, temperature=0.2)
        edits = parse_edits(gen.text)
        for _ in range(self.max_format_retries):
            if edits:
                break
            gen = self.provider.generate_one(
                prompt + "\n\nYour previous reply contained no SEARCH/REPLACE blocks. "
                "Reply with ONLY the ```python blocks and one explanation paragraph.",
                system=SYSTEM, max_tokens=1500, temperature=0.5)
            edits = parse_edits(gen.text)
        if not edits:
            return Patch(hypothesis_id=hypothesis.id, reasoning=gen.text[:1000],
                         expected_test_effect=hypothesis.expected_behavior,
                         valid=False, error="no parseable SEARCH/REPLACE blocks",
                         input_tokens=gen.input_tokens, output_tokens=gen.output_tokens,
                         cost_usd=gen.cost_usd, is_refinement=bool(previous_error))

        files, symbols, diffs, new_contents = [], list(hypothesis.affected_symbols), [], {}
        overall_tier = MatchStatus.EXACT.value
        ast_repaired = False

        for file, file_edits in edits.items():
            original = self.retriever.read_raw(file)
            if not original.strip():
                return Patch(hypothesis_id=hypothesis.id, valid=False,
                             error=f"file not in checkout: {file}",
                             input_tokens=gen.input_tokens,
                             output_tokens=gen.output_tokens, cost_usd=gen.cost_usd,
                             is_refinement=bool(previous_error))
            new, tier, error = apply_edits_detailed(original, file_edits)
            if error:
                return Patch(hypothesis_id=hypothesis.id, valid=False,
                             error=f"{file}: {error}", match_tier=tier,
                             input_tokens=gen.input_tokens,
                             output_tokens=gen.output_tokens, cost_usd=gen.cost_usd,
                             is_refinement=bool(previous_error))
            
            # AST Pre-Validation & 1-shot syntax repair
            ast_ok, ast_err = validate_ast(new, file)
            if not ast_ok and self.max_syntax_retries > 0:
                syntax_prompt = (
                    f"The patch applied to `{file}` resulted in a Python syntax error:\n"
                    f"{ast_err}\n\n"
                    "Original patch text was:\n"
                    f"```\n{gen.text[:1000]}\n```\n\n"
                    "Please provide a corrected SEARCH/REPLACE block that fixes the syntax error. "
                    "Do NOT use '...' or placeholder expressions."
                )
                repair_gen = self.provider.generate_one(
                    syntax_prompt, system=SYSTEM, max_tokens=1500, temperature=0.1)
                repaired_edits = parse_edits(repair_gen.text)
                if file in repaired_edits:
                    rep_new, rep_tier, rep_err = apply_edits_detailed(original, repaired_edits[file])
                    rep_ast_ok, rep_ast_err = validate_ast(rep_new, file)
                    if not rep_err and rep_ast_ok:
                        new = rep_new
                        tier = rep_tier
                        ast_ok = True
                        ast_repaired = True
                        gen.input_tokens += repair_gen.input_tokens
                        gen.output_tokens += repair_gen.output_tokens
                        gen.cost_usd += repair_gen.cost_usd

            if not ast_ok:
                return Patch(hypothesis_id=hypothesis.id, valid=False,
                             error=f"{file} syntax error: {ast_err}", match_tier=tier,
                             input_tokens=gen.input_tokens,
                             output_tokens=gen.output_tokens, cost_usd=gen.cost_usd,
                             is_refinement=bool(previous_error))

            overall_tier = tier
            files.append(file)
            new_contents[file] = new
            diffs.append("".join(difflib.unified_diff(
                original.splitlines(True), new.splitlines(True),
                fromfile="a/" + file, tofile="b/" + file)))

        reasoning = gen.text.split("```")[-1].strip()[:1000]
        return Patch(hypothesis_id=hypothesis.id, files_changed=files,
                     symbols_changed=symbols, patch_text="".join(diffs),
                     reasoning=reasoning,
                     expected_test_effect=hypothesis.expected_behavior,
                     valid=True, match_tier=overall_tier,
                     ast_repair_used=ast_repaired,
                     is_refinement=bool(previous_error),
                     input_tokens=gen.input_tokens,
                     output_tokens=gen.output_tokens, cost_usd=gen.cost_usd,
                     new_contents=new_contents)
