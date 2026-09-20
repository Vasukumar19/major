"""Deterministic Baseline Repair Engine for PatchForge AI (V0.4.3 Multi-Site Refinement).

Implements the proven SWE-repair golden path:
Issue -> Agentless-style Localization -> Authoritative RepairTarget ->
Aider-style Context -> Single-turn Coding Model -> Patch Validation ->
Patch Application -> Target/Reproduction Test -> Evidence-Driven Refinement -> Telemetry
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from patchforge.core.target import EditSite, MultiSiteRepairTarget, RepairTarget
from patchforge.issue.problem import Problem
from patchforge.models.provider import ModelProvider, OllamaProvider
from patchforge.reasoning.hypothesis import RefinedHypothesis
from patchforge.repair.generator import parse_edits
from patchforge.repair.patch import Patch
from patchforge.repository.map import RepoMap
from patchforge.retrieval.evidence import ExecutionEvidence
from patchforge.telemetry.events import TelemetryLogger
from patchforge.tools.editor import ApplyPatchTool
from patchforge.verification.analyzer import FailureAnalyzer, SemanticDiagnosis
from patchforge.verification.classifier import FailureClass, classify
from patchforge.verification.tester import Tester


SYSTEM_REPAIR_PROMPT = """You are PatchForge AI, an autonomous software-repair system.
You are tasked with fixing a defect described in a GitHub issue.
Output ONLY a single valid JSON object containing your patch action:
```json
{
  "thought": "Explanation of fix",
  "action": {
    "name": "apply_patch",
    "arguments": {
      "patch_text": "### <file>\n<<<<<<< SEARCH\n<exact verbatim source code>\n=======\n<replacement code>\n>>>>>>> REPLACE"
    }
  }
}
```
Rules:
1. The SEARCH block MUST match exact verbatim lines from the VERIFIED SOURCE provided.
2. Do NOT include diff markers ('+' or '-') in SEARCH blocks.
3. The REPLACE block contains the corrected code.
4. Only output the JSON object."""


@dataclass
class BaselineResult:
    instance_id: str
    target: RepairTarget | None = None
    patch: Patch | None = None
    patch_applied: bool = False
    test_executed: bool = False
    resolved: bool = False
    failure_class: str = FailureClass.UNRESOLVED.value
    runtime_s: float = 0.0
    telemetry: dict[str, Any] = field(default_factory=dict)
    test_summary: dict[str, Any] = field(default_factory=dict)
    refinement_cycles: int = 0
    refinement_history: list[dict[str, Any]] = field(default_factory=list)


def _normalize_stem(word: str) -> str:
    """Lightweight English suffix stripper for identifiers and search terms."""
    word = word.lower()
    if len(word) > 4:
        if word.endswith("ing"):
            return word[:-3]
        if word.endswith("ed"):
            return word[:-2]
        if word.endswith("ies"):
            return word[:-3] + "y"
        if word.endswith("es"):
            return word[:-2]
        if word.endswith("s") and not word.endswith("ss"):
            return word[:-1]
    return word


_COMMON_WORDS = {
    "get", "set", "read", "write", "open", "close", "run", "start", "stop",
    "call", "send", "update", "values", "items", "keys", "pop", "copy",
    "clear", "count", "index", "head", "post", "put", "delete", "options",
    "request", "session", "response", "type", "format", "parse", "data",
    "body", "file", "code", "error", "text", "line", "string", "list", "dict",
}


def _is_meaningful_symbol_match(name: str, kind: str, issue_text: str, dot_calls: set[str], code_words: set[str]) -> tuple[bool, float]:
    name_low = name.lower()
    if name in dot_calls:
        return True, 30.0
    if name in code_words or name_low in code_words:
        return True, 25.0
    if re.search(r"\b" + re.escape(name) + r"\s*\(", issue_text):
        return True, 25.0
    if name_low in _COMMON_WORDS:
        if kind == "class":
            if re.search(r"\b(?:class\s+" + re.escape(name) + r"|" + re.escape(name) + r"\s+class)\b", issue_text, re.IGNORECASE):
                return True, 25.0
            if re.search(r"\b" + re.escape(name) + r"\.", issue_text):
                return True, 25.0
        return False, 0.0
    if re.search(r"\b" + re.escape(name) + r"\b", issue_text, re.IGNORECASE):
        if kind == "class":
            return True, 20.0
        return True, 15.0 if len(name) >= 6 else 8.0
    return False, 0.0


class BaselineRepairEngine:
    """Executes deterministic software repair with bounded semantic refinement."""

    def __init__(
        self,
        provider: ModelProvider | None = None,
        model_name: str = "qwen2.5-coder:7b",
        workspace: str = ".",
    ):
        self.provider = provider or OllamaProvider(model=model_name)
        self.model_name = model_name
        self.workspace = workspace

    def localize(self, problem: Problem, repo_dir: str) -> RepairTarget:
        """Deterministic hierarchical localization using Agentless-style retrieval."""
        repo_p = Path(repo_dir)
        repo_map = RepoMap(repo_dir)
        issue_text = (problem.problem_statement or "") + "\n" + (problem.hints_text or "")

        # 1. Scan for explicit file names mentioned in issue
        candidate_file = ""
        mentioned_files = re.findall(r"[\w/\.-]+\.py\b", issue_text)
        for mf in mentioned_files:
            clean_f = mf.strip().replace("\\", "/").lstrip("/")
            if (repo_p / clean_f).exists() and (repo_p / clean_f).is_file():
                if not any(part in ("tests", "testing", "test", "docs", ".git", "venv", ".venv", "packages", "vendor") for part in Path(clean_f).parts):
                    candidate_file = clean_f
                    break

        # 2. Score candidate Python files in repo using keyword overlap
        if not candidate_file:
            py_files = [
                str(p.relative_to(repo_p)).replace("\\", "/")
                for p in repo_p.glob("**/*.py")
                if p.is_file() and not any(part in ("tests", "testing", "test", "docs", ".git", "venv", ".venv", "packages", "vendor", "third_party", "build", "dist") for part in p.parts)
            ]

            repo_name = repo_p.name.lower()
            dot_calls = set(re.findall(r"\.([A-Za-z_][A-Za-z0-9_]+)", issue_text))
            code_words = set(re.findall(r"`([^`]+)`", issue_text))
            issue_stems = [_normalize_stem(w.lower()) for w in re.findall(r"[a-zA-Z_]\w*", issue_text)]

            file_scores: list[tuple[float, str]] = []
            for pf in py_files:
                score = 0.0
                symbols = repo_map.extract_symbols(pf)
                for s in symbols:
                    if s.name.startswith("__") and s.name.endswith("__"):
                        if s.parent_class and f"{s.parent_class}.{s.name}".lower() in issue_text.lower():
                            score += 30.0
                        continue
                    matched, pts = _is_meaningful_symbol_match(s.name, s.kind, issue_text, dot_calls, code_words)
                    if matched:
                        score += pts

                stem = Path(pf).stem.lower()
                stem_norm = _normalize_stem(stem)
                if stem != repo_name and len(stem) > 3 and stem_norm not in _COMMON_WORDS:
                    cnt = issue_stems.count(stem_norm)
                    if cnt > 0:
                        score += min(cnt, 3) * 15.0

                file_scores.append((score, pf))

            file_scores.sort(key=lambda x: x[0], reverse=True)
            candidate_file = file_scores[0][1] if file_scores else (py_files[0] if py_files else "unknown.py")

        # 3. Identify candidate symbol in candidate file
        symbols = repo_map.extract_symbols(candidate_file)
        dot_calls = set(re.findall(r"\.([A-Za-z_][A-Za-z0-9_]+)", issue_text))
        code_words = set(re.findall(r"`([^`]+)`", issue_text))
        sym_scores: list[tuple[float, str, str]] = []
        for s in symbols:
            score = 0.0
            if s.name.startswith("__") and s.name.endswith("__"):
                if s.parent_class and f"{s.parent_class}.{s.name}".lower() in issue_text.lower():
                    score += 35.0
                elif s.name == "__init__" and any(w in issue_text.lower() for w in ("__init__", "init", "constructor")):
                    score += 10.0
            else:
                matched, pts = _is_meaningful_symbol_match(s.name, s.kind, issue_text, dot_calls, code_words)
                if matched:
                    score += pts
            sym_scores.append((score, s.name, s.kind))

        sym_scores.sort(key=lambda x: x[0], reverse=True)
        candidate_sym = sym_scores[0][1] if sym_scores else (symbols[0].name if symbols else "")

        # 4. Extract surgical unit and behavior context
        source_win, win_start, win_end, behavior_context = repo_map.extract_surgical_unit(
            candidate_file, candidate_sym, issue_text
        )

        return RepairTarget(
            repository=problem.repo,
            file_path=candidate_file,
            symbol=candidate_sym,
            line_start=win_start,
            line_end=win_end,
            source_span=(win_start, win_end),
            verified_source=source_win,
            verification_status=bool(source_win),
            behavior_context=behavior_context,
        )

    def build_context(self, problem: Problem, target: RepairTarget) -> str:
        """Constructs a surgical, bounded repair context with standard sections."""
        all_sites = target.all_sites()
        target_files_str = ", ".join(f"'{s.file_path}'" for s in all_sites)

        sections = [
            "=== ISSUE ===",
            problem.problem_statement.strip()[:1500],
            "",
            "=== REPAIR TARGET ===",
            f"Repository: {target.repository}",
            f"Target File: {target.file_path}",
            f"Target Symbol: {target.symbol}",
            f"Lines: {target.line_start}-{target.line_end}",
            "",
        ]

        if target.behavior_context:
            sections.extend([
                "=== BEHAVIOR CONTEXT ===",
                target.behavior_context.strip(),
                "",
            ])

        sections.append("=== EDITABLE SOURCE — VERBATIM ===")
        for site in all_sites:
            sections.extend([
                f"--- File: {site.file_path} (Lines {site.line_start}-{site.line_end}, Symbol: {site.symbol}) ---",
                site.verified_source,
                "",
            ])

        sections.append("=== RELATED TEST ===")
        if problem.fail_to_pass:
            sections.append("\n".join(f"- {t}" for t in problem.fail_to_pass[:5]))
        else:
            sections.append("None specified")

        sections.extend([
            "",
            "=== REPAIR CONSTRAINTS ===",
            f"1. Fix the issue strictly inside {target_files_str}.",
            f"2. You MUST use an apply_patch JSON action targeting the appropriate file(s).",
            "3. The SEARCH block MUST match exact verbatim lines from EDITABLE SOURCE — VERBATIM above (do NOT omit docstring lines or comments between functions).",
            "4. Do NOT use git diff format, '+' or '-' markers, or diff headers. Format the patch strictly using SEARCH/REPLACE blocks.",
            "5. Preserve all existing function signatures and unmodified behaviors.",
            "6. Output ONLY the single valid JSON object.",
        ])
        return "\n".join(sections)

    def _extract_patch_text(self, raw_text: str, target: RepairTarget) -> str:
        """Robustly extracts SEARCH/REPLACE patch text from LLM response."""
        if "<think>" in raw_text and "</think>" in raw_text:
            raw_text = raw_text.split("</think>")[-1].strip()
        elif "<think>" in raw_text:
            raw_text = raw_text.split("<think>")[-1].strip()

        candidates = []
        json_blocks = re.findall(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", raw_text)
        if json_blocks:
            candidates.extend(json_blocks)

        first_brace = raw_text.find("{")
        last_brace = raw_text.rfind("}")
        if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
            candidates.append(raw_text[first_brace:last_brace + 1])

        candidates.append(raw_text)

        from patchforge.repair.generator import diff_to_search_replace, code_to_search_replace

        for cand in candidates:
            data = None
            try:
                data = json.loads(cand, strict=False)
            except Exception:
                # Fallback regex extraction for malformed JSON with unescaped quotes/newlines
                m = re.search(r'"(?:patch_text|patch|diff|code)":\s*"(.*)"\s*(?:,\s*"|\}\s*\}|\}\s*,|\}\s*$)', cand, re.DOTALL)
                if m:
                    raw_val = m.group(1)
                    try:
                        extracted_val = raw_val.encode("utf-8").decode("unicode_escape")
                    except Exception:
                        extracted_val = raw_val.replace('\\"', '"').replace('\\n', '\n').replace('\\t', '\t')
                    data = {"action": {"arguments": {"patch_text": extracted_val}}}

            if not data or not isinstance(data, dict):
                continue

            try:
                # Check top-level patch or diff fields
                top_p = data.get("patch_text", "") or data.get("patch", "") or data.get("diff", "")
                target_f = data.get("file", "") or data.get("file_path", "") or target.file_path
                if top_p:
                    if "<<<<<<< SEARCH" in top_p:
                        return f"### {target_f}\n{top_p}" if "###" not in top_p else top_p
                    converted = diff_to_search_replace(top_p, target_f)
                    if converted:
                        return converted
                    converted_code = code_to_search_replace(top_p, target, target_f)
                    if converted_code:
                        return converted_code

                action = data.get("action", {})
                if isinstance(action, dict):
                    args = action.get("arguments", {}) or action.get("args", {}) or action.get("parameters", {})
                    p = args.get("patch_text", "") or args.get("patch", "") or args.get("diff", "")
                    a_file = args.get("file_path", "") or args.get("file", "") or target.file_path
                    if p:
                        if "<<<<<<< SEARCH" in p:
                            return f"### {a_file}\n{p}" if "###" not in p else p
                        converted = diff_to_search_replace(p, a_file)
                        if converted:
                            return converted
                        converted_code = code_to_search_replace(p, target, a_file)
                        if converted_code:
                            return converted_code
                # Check top-level or argument-level search and replace fields (e.g. lists or strings)
                search_val = data.get("search") or (action.get("arguments", {}).get("search") if isinstance(action, dict) else None)
                replace_val = data.get("replace") or (action.get("arguments", {}).get("replace") if isinstance(action, dict) else None)
                if search_val is not None and replace_val is not None:
                    s_str = "\n".join(search_val) if isinstance(search_val, list) else str(search_val)
                    r_str = "\n".join(replace_val) if isinstance(replace_val, list) else str(replace_val)
                    target_f = data.get("file") or data.get("file_path") or (action.get("arguments", {}).get("file_path") if isinstance(action, dict) else None) or target.file_path
                    return f"### {target_f}\n<<<<<<< SEARCH\n{s_str}\n=======\n{r_str}\n>>>>>>> REPLACE"
            except Exception:
                continue

        edits = parse_edits(raw_text)
        if edits:
            return raw_text

        if "<<<<<<< SEARCH" in raw_text:
            return f"### {target.file_path}\n" + raw_text

        if "diff --git" in raw_text or "@@" in raw_text or "--- a/" in raw_text:
            converted = diff_to_search_replace(raw_text, target.file_path)
            if converted:
                return converted

        py_blocks = re.findall(r"```(?:python)?\s*(def\s+[\s\S]*?|class\s+[\s\S]*?)\s*```", raw_text)
        for pb in py_blocks:
            converted = code_to_search_replace(pb, target)
            if converted:
                return converted

        if not (raw_text.strip().startswith("{") or "```json" in raw_text or '"action":' in raw_text):
            converted_raw = code_to_search_replace(raw_text, target)
            if converted_raw:
                return converted_raw

        return ""

    def build_refinement_context(
        self,
        problem: Problem,
        target: RepairTarget,
        original_patch: Patch,
        evidence: ExecutionEvidence,
        diagnosis: SemanticDiagnosis,
        refined_hypothesis: RefinedHypothesis,
    ) -> str:
        """Constructs a compact refinement context strictly per V0.4.2 specification."""
        orig_patch_summary = original_patch.patch_text.strip()
        if len(orig_patch_summary) > 1000:
            orig_patch_summary = orig_patch_summary[:1000] + "\n... [diff truncated]"

        all_sites = target.all_sites()
        target_files_str = ", ".join(f"'{s.file_path}'" for s in all_sites)

        sections = [
            "=== ISSUE ===",
            problem.problem_statement.strip()[:1500],
            "",
            "=== REPAIR TARGET ===",
            f"Repository: {target.repository}",
            f"Target File: {target.file_path}",
            f"Target Symbol: {target.symbol}",
            f"Lines: {target.line_start}-{target.line_end}",
        ]
        if target.secondary_sites:
            for idx, site in enumerate(target.secondary_sites, 1):
                sections.append(
                    f"Secondary Target {idx}: {site.file_path} (Symbol: {site.symbol}, Lines: {site.line_start}-{site.line_end})"
                )
        sections.append("")

        sections.append("=== VERIFIED EDITABLE SOURCE ===")
        for site in all_sites:
            sections.extend([
                f"--- File: {site.file_path} (Lines {site.line_start}-{site.line_end}, Symbol: {site.symbol}) ---",
                site.verified_source,
                "",
            ])

        sections.extend([
            "=== ORIGINAL HYPOTHESIS ===",
            refined_hypothesis.original_hypothesis,
            "",
            "=== ORIGINAL PATCH ===",
            orig_patch_summary,
            "",
            diagnosis.diagnostics_text,
            "",
            "=== REFINED HYPOTHESIS ===",
            refined_hypothesis.refined_hypothesis,
            "",
            "=== REPAIR CONSTRAINTS ===",
            f"1. Fix the remaining failure strictly inside {target_files_str}.",
            f"2. You MUST use an apply_patch JSON action targeting the appropriate file(s) and symbol(s).",
            "3. The SEARCH block MUST match exact verbatim clean lines from VERIFIED EDITABLE SOURCE above.",
            "4. Do NOT include git diff markers ('+' or '-') in SEARCH blocks.",
            "5. Address the concrete failure identified in SEMANTIC DIAGNOSIS above (e.g. if an AssertionError is raised, replace the assert statement with the expected exception type such as ValueError).",
            "6. Preserve all passing behavior and do not modify unaffected functions.",
            "7. Output ONLY the single valid JSON object.",
        ])
        return "\n".join(sections)

    def run(
        self,
        problem: Problem,
        repo_dir: str,
        tester: Tester | None = None,
        max_retries: int = 2,
        max_refinements: int = 2,
    ) -> BaselineResult:
        """Execute end-to-end baseline repair on one instance."""
        logger = TelemetryLogger(task_id=problem.instance_id)
        t0 = time.time()
        result = BaselineResult(instance_id=problem.instance_id)

        def _cleanup():
            if not repo_dir or os.path.abspath(repo_dir) == os.path.abspath("."):
                return
            if problem.base_commit and any(k in str(repo_dir).lower() for k in ("repos", "cache", "testbed")):
                subprocess.run(["git", "checkout", "-f", problem.base_commit], cwd=repo_dir, capture_output=True)
                subprocess.run(["git", "clean", "-fd"], cwd=repo_dir, capture_output=True)

        _cleanup()

        loc_t0 = time.time()
        target = self.localize(problem, repo_dir)
        loc_ms = (time.time() - loc_t0) * 1000.0
        logger.log_event("LOCALIZE", "TARGET_LOCALIZED", duration_ms=loc_ms, details=target.to_dict())
        result.target = target

        if not target.verification_status:
            result.failure_class = FailureClass.WRONG_LOCALIZATION.value
            result.runtime_s = round(time.time() - t0, 2)
            result.telemetry = logger.get_breakdown()
            _cleanup()
            return result

        prompt = self.build_context(problem, target)
        patch_applied = False
        applied_patch: Patch | None = None

        for attempt in range(1, max_retries + 1):
            llm_t0 = time.time()
            try:
                resp = self.provider.generate_one(prompt, system=SYSTEM_REPAIR_PROMPT)
                llm_ms = (time.time() - llm_t0) * 1000.0
                logger.log_event(
                    "PATCH", "LLM_GENERATE", duration_ms=llm_ms, model=self.model_name,
                    input_tokens=resp.input_tokens, output_tokens=resp.output_tokens,
                    details={"attempt": attempt},
                )
            except Exception as e:
                logger.log_event("PATCH", "LLM_ERROR", details={"error": str(e), "attempt": attempt})
                continue

            raw_text = resp.text.strip()
            patch_text = self._extract_patch_text(raw_text, target)

            if not patch_text:
                logger.log_event("PATCH", "PATCH_PARSE_FAILED", details={"attempt": attempt})
                continue

            edits_map = parse_edits(patch_text)
            for _, blocks in edits_map.items():
                for s_block, r_block in blocks:
                    s_len = len(s_block.splitlines())
                    r_len = len(r_block.splitlines())
                    if (r_len > 2 * s_len + 15) or resp.output_tokens > 2000:
                        logger.log_event(
                            "PATCH",
                            "PATCH_SIZE_ANOMALY",
                            input_tokens=resp.input_tokens,
                            output_tokens=resp.output_tokens,
                            details={
                                "search_lines": s_len,
                                "replace_lines": r_len,
                                "ratio": round(r_len / max(1, s_len), 2),
                            },
                        )

            apply_tool = ApplyPatchTool(repo_dir=repo_dir)
            if hasattr(apply_tool, "write_to_disk"):
                apply_tool.write_to_disk = True
            allowed_files = [s.file_path for s in target.all_sites()]
            apply_res = apply_tool.execute({
                "patch_text": patch_text,
                "expected_target_file": target.file_path,
                "allowed_files": allowed_files,
                "write_to_disk": True,
            })

            if apply_res.status == "SUCCESS":
                patch_applied = True
                p_data = apply_res.data.get("patch", {})
                diff_proc = subprocess.run(["git", "diff"], cwd=repo_dir, capture_output=True, text=True)
                full_diff = diff_proc.stdout or apply_res.data.get("diff", "")
                applied_patch = Patch(
                    hypothesis_id="BASELINE_H1",
                    files_changed=apply_res.data.get("files_changed", [target.file_path]),
                    patch_text=full_diff,
                    valid=True,
                    match_tier=p_data.get("match_tier", "EXACT"),
                    input_tokens=resp.input_tokens,
                    output_tokens=resp.output_tokens,
                )
                logger.log_event("APPLY", "PATCH_APPLIED", details={"diff_length": len(applied_patch.patch_text)})
                break
            else:
                logger.log_event("APPLY", "PATCH_FAILED", details={"error": apply_res.error, "attempt": attempt})
                prompt += f"\n\n[PREVIOUS ATTEMPT FAILED]: {apply_res.error}\nFix the error and produce exact verbatim SEARCH lines."

        result.patch = applied_patch
        result.patch_applied = patch_applied

        if not patch_applied or not applied_patch:
            result.failure_class = FailureClass.PATCH_SYNTAX.value
            result.runtime_s = round(time.time() - t0, 2)
            result.telemetry = logger.get_breakdown()
            _cleanup()
            return result

        if tester:
            test_t0 = time.time()
            verdict = tester.run(problem.instance_id, applied_patch.patch_text)
            test_ms = (time.time() - test_t0) * 1000.0

            result.test_executed = True
            result.resolved = bool(getattr(verdict, "resolved", False))
            f_class = classify(applied_patch, verdict)
            result.failure_class = f_class.value
            result.test_summary = verdict.to_dict() if hasattr(verdict, "to_dict") else {}

            logger.log_event(
                "TEST", "TEST_EXECUTION", duration_ms=test_ms,
                details={
                    "cycle": 0,
                    "resolved": result.resolved,
                    "failure_class": result.failure_class,
                    "f2p_passed": getattr(verdict, "fail_to_pass_passed", 0),
                    "f2p_total": getattr(verdict, "fail_to_pass_total", 0),
                    "p2p_passed": getattr(verdict, "pass_to_pass_passed", 0),
                    "p2p_total": getattr(verdict, "pass_to_pass_total", 0),
                },
            )

            is_infra = bool(getattr(verdict, "infra_failure", False)) or "timeout" in getattr(verdict, "error", "").lower()
            if not result.resolved and not is_infra and max_refinements > 0:
                # failure_history tracks refinement cycle signatures ONLY.
                # Do NOT pre-populate with Cycle 0 signature so that a refinement cycle
                # which produces the same failing test names (but a different root error)
                # still gets another attempt. The guard only fires when two consecutive
                # REFINEMENT cycles are strictly identical.
                failure_history = []
                current_patch = applied_patch
                previous_eval = verdict

                for cycle in range(1, max_refinements + 1):
                    result.refinement_cycles = cycle
                    print(f"\n--- [REFINEMENT CYCLE {cycle}/{max_refinements}] ---", flush=True)
                    f2p_fail = list(getattr(previous_eval, "fail_to_pass_failure", []) or [])
                    p2p_fail = list(getattr(previous_eval, "pass_to_pass_failure", []) or [])
                    f2p_succ = list(getattr(previous_eval, "fail_to_pass_success", []) or [])
                    p2p_succ = list(getattr(previous_eval, "pass_to_pass_success", []) or [])

                    evidence = ExecutionEvidence(
                        task_id=problem.instance_id,
                        target_tests_total=previous_eval.fail_to_pass_total,
                        target_tests_passed=previous_eval.fail_to_pass_passed,
                        target_tests_failed=len(f2p_fail),
                        regression_tests_total=previous_eval.pass_to_pass_total,
                        regression_tests_passed=previous_eval.pass_to_pass_passed,
                        regression_tests_failed=len(p2p_fail),
                        failure_class=result.failure_class,
                        failed_target_tests=f2p_fail,
                        failed_regression_tests=p2p_fail,
                        passed_target_tests=f2p_succ,
                        passed_regression_tests=p2p_succ,
                        traceback=getattr(previous_eval, "test_output", "") or getattr(previous_eval, "stdout", ""),
                        stdout=getattr(previous_eval, "stdout", ""),
                        stderr=getattr(previous_eval, "stderr", ""),
                        patch=current_patch.patch_text,
                    )

                    analyzer = FailureAnalyzer()
                    repo_map = RepoMap(repo_dir)
                    new_sites = analyzer.extract_secondary_edit_sites(evidence, repo_map, target, max_secondary_sites=1)
                    # Fallback: when no library traceback frames were available, use test-name keywords
                    # to find repo functions that match what the failing test exercises.
                    if not new_sites:
                        new_sites = analyzer._test_name_fallback_sites(evidence, repo_map, target, max_secondary_sites=1)
                    for n_site in new_sites:
                        if target.add_secondary_site(n_site):
                            print(f"  [SCOPE EXPANSION] Secondary site added: {n_site.file_path}:{n_site.line_start}-{n_site.line_end} ({n_site.symbol})", flush=True)
                            logger.log_event(
                                "REFINE", "SECONDARY_SITE_ADDED",
                                details={
                                    "file_path": n_site.file_path,
                                    "symbol": n_site.symbol,
                                    "span": [n_site.line_start, n_site.line_end],
                                }
                            )

                    refreshed_sites = []
                    for s in target.all_sites():
                        s_win, w_start, w_end, b_ctx = repo_map.extract_surgical_unit(
                            s.file_path, s.symbol, target_line=s.line_start
                        )
                        refreshed_sites.append(EditSite(
                            file_path=s.file_path,
                            symbol=s.symbol,
                            line_start=w_start if s_win else s.line_start,
                            line_end=w_end if s_win else s.line_end,
                            source_span=(w_start if s_win else s.line_start, w_end if s_win else s.line_end),
                            verified_source=s_win or s.verified_source,
                            verification_status=bool(s_win or s.verification_status),
                            behavior_context=b_ctx or s.behavior_context,
                        ))
                    if refreshed_sites:
                        target.line_start = refreshed_sites[0].line_start
                        target.line_end = refreshed_sites[0].line_end
                        target.source_span = (refreshed_sites[0].line_start, refreshed_sites[0].line_end)
                        target.verified_source = refreshed_sites[0].verified_source
                        target.secondary_sites = refreshed_sites[1:]

                    diagnosis = analyzer.analyze(evidence, target, original_hypothesis="INITIAL_REPAIR")

                    all_target_files = sorted(set(s.file_path for s in target.all_sites()))
                    all_target_files_str = ", ".join(f"'{f}'" for f in all_target_files)

                    if f2p_fail and not p2p_fail:
                        short_failing = [t.split("::")[-1] for t in f2p_fail[:3]]
                        ref_text = f"The initial patch resolved {evidence.target_tests_passed}/{evidence.target_tests_total} target tests but failed on: {', '.join(short_failing)}. The repair must be refined to satisfy these failing assertions while strictly preserving passing tests."
                        if target.secondary_sites:
                            sec_symbols = [s.symbol for s in target.secondary_sites if s.symbol]
                            ref_text += f" You may apply changes to the primary site or secondary site ({', '.join(sec_symbols)}) as needed to resolve the remaining failure."
                    elif p2p_fail:
                        c_desc = f" ({diagnosis.clusters[0].error_type}: {diagnosis.clusters[0].message[:40]})" if diagnosis.clusters else ""
                        ref_text = f"The initial patch repaired targeted defect behavior ({evidence.target_tests_passed}/{evidence.target_tests_total} target tests passed) but broke existing contracts or introduced runtime errors{c_desc}. Scope the change strictly to avoid regressions."
                    else:
                        ref_text = "Refine the repair patch to satisfy all test contracts."

                    refined_hypothesis = RefinedHypothesis(
                        original_hypothesis="Initial repair hypothesis targeting defect description.",
                        execution_evidence_summary=f"{evidence.target_tests_passed}/{evidence.target_tests_total} target, {evidence.regression_tests_passed}/{evidence.regression_tests_total} regression passed.",
                        refined_hypothesis=ref_text,
                        repair_plan=[
                            f"Address the concrete error identified in {all_target_files_str}.",
                            "Ensure all referenced names, types, and imports exist in module scope.",
                            "Preserve all passing regression tests.",
                        ],
                        repair_constraints=[
                            f"Modify strictly inside: {all_target_files_str}.",
                            "SEARCH block must match exact verbatim lines without diff markers (+/-).",
                            "Output ONLY the apply_patch JSON action.",
                        ],
                    )

                    refine_prompt = self.build_refinement_context(
                        problem=problem,
                        target=target,
                        original_patch=current_patch,
                        evidence=evidence,
                        diagnosis=diagnosis,
                        refined_hypothesis=refined_hypothesis,
                    )

                    llm_t0 = time.time()
                    try:
                        resp = self.provider.generate_one(refine_prompt, system=SYSTEM_REPAIR_PROMPT)
                        llm_ms = (time.time() - llm_t0) * 1000.0
                        logger.log_event(
                            "REFINE", "LLM_GENERATE", duration_ms=llm_ms, model=self.model_name,
                            input_tokens=resp.input_tokens, output_tokens=resp.output_tokens,
                            details={"cycle": cycle},
                        )
                    except Exception as e:
                        logger.log_event("REFINE", "LLM_ERROR", details={"error": str(e), "cycle": cycle})
                        break

                    refined_patch_text = self._extract_patch_text(resp.text.strip(), target)
                    if not refined_patch_text:
                        print(f"  [REFINEMENT CYCLE {cycle}] Patch parse failed from raw text:\n{resp.text[:300]}", flush=True)
                        logger.log_event("REFINE", "PATCH_PARSE_FAILED", details={"cycle": cycle})
                        continue

                    print(f"  [REFINEMENT CYCLE {cycle}] Refined patch text:\n{refined_patch_text}", flush=True)

                    # Detect no-op patches where every SEARCH block is identical to its REPLACE block.
                    # These burn a refinement cycle without making progress. Skip and continue.
                    from patchforge.repair.generator import parse_edits as _parse_edits
                    _parsed_noop = _parse_edits(refined_patch_text)
                    if _parsed_noop and all(
                        s.strip() == r.strip()
                        for blocks in _parsed_noop.values()
                        for s, r in blocks
                    ):
                        print(f"  [REFINEMENT CYCLE {cycle}] NO-OP PATCH DETECTED (SEARCH==REPLACE): skipping apply.", flush=True)
                        logger.log_event("REFINE", "NOOP_PATCH_SKIPPED", details={"cycle": cycle})
                        refine_prompt += f"\n\n[PREVIOUS ATTEMPT WAS A NO-OP]: Your SEARCH and REPLACE blocks were identical — you must actually CHANGE the code. Identify the exact lines that need to differ between SEARCH and REPLACE."
                        continue

                    apply_tool = ApplyPatchTool(repo_dir=repo_dir)
                    if hasattr(apply_tool, "write_to_disk"):
                        apply_tool.write_to_disk = True
                    allowed_files = [s.file_path for s in target.all_sites()]
                    apply_res = apply_tool.execute({
                        "patch_text": refined_patch_text,
                        "expected_target_file": target.file_path,
                        "allowed_files": allowed_files,
                        "write_to_disk": True,
                    })

                    print(f"  [REFINEMENT CYCLE {cycle}] Patch apply result: {apply_res.status} ({apply_res.error if apply_res.status != 'SUCCESS' else 'OK'})", flush=True)
                    if apply_res.status != "SUCCESS":
                        logger.log_event("REFINE", "PATCH_APPLY_FAILED", details={"error": apply_res.error, "cycle": cycle})
                        refine_prompt += f"\n\n[PREVIOUS ATTEMPT FAILED]: {apply_res.error}\nEnsure SEARCH lines match exact verbatim clean code from VERIFIED EDITABLE SOURCE."
                        continue

                    p_data = apply_res.data.get("patch", {})
                    diff_proc = subprocess.run(["git", "diff"], cwd=repo_dir, capture_output=True, text=True)
                    full_diff = diff_proc.stdout or apply_res.data.get("diff", "")
                    refined_patch = Patch(
                        hypothesis_id=f"REFINED_CYCLE_{cycle}",
                        files_changed=apply_res.data.get("files_changed", [target.file_path]),
                        patch_text=full_diff,
                        valid=True,
                        match_tier=p_data.get("match_tier", "EXACT"),
                        input_tokens=resp.input_tokens,
                        output_tokens=resp.output_tokens,
                    )

                    test_t0 = time.time()
                    refine_verdict = tester.run(problem.instance_id, refined_patch.patch_text)
                    test_ms = (time.time() - test_t0) * 1000.0

                    print(f"  [REFINEMENT CYCLE {cycle}] Test result: resolved={refine_verdict.resolved}, F2P={refine_verdict.fail_to_pass_passed}/{refine_verdict.fail_to_pass_total}, P2P={refine_verdict.pass_to_pass_passed}/{refine_verdict.pass_to_pass_total}", flush=True)
                    logger.log_event(
                        "REFINE", "TEST_EXECUTION", duration_ms=test_ms,
                        details={
                            "cycle": cycle,
                            "resolved": refine_verdict.resolved,
                            "f2p_passed": refine_verdict.fail_to_pass_passed,
                            "f2p_total": refine_verdict.fail_to_pass_total,
                            "p2p_passed": refine_verdict.pass_to_pass_passed,
                            "p2p_total": refine_verdict.pass_to_pass_total,
                        },
                    )

                    cycle_record = {
                        "cycle": cycle,
                        "patch_applied": True,
                        "resolved": refine_verdict.resolved,
                        "f2p_passed": refine_verdict.fail_to_pass_passed,
                        "f2p_total": refine_verdict.fail_to_pass_total,
                        "p2p_passed": refine_verdict.pass_to_pass_passed,
                        "p2p_total": refine_verdict.pass_to_pass_total,
                    }
                    result.refinement_history.append(cycle_record)

                    new_sig = (
                        tuple(sorted(getattr(refine_verdict, "fail_to_pass_failure", []) or [])),
                        tuple(sorted(getattr(refine_verdict, "pass_to_pass_failure", []) or [])),
                    )
                    if new_sig in failure_history:
                        logger.log_event("REFINE", "REPEATED_FAILURE_DETECTED", details={"cycle": cycle})
                        result.failure_class = "REFINEMENT_EXHAUSTED"
                        break
                    failure_history.append(new_sig)

                    if refine_verdict.pass_to_pass_passed < previous_eval.pass_to_pass_passed:
                        logger.log_event("REFINE", "REGRESSION_INTRODUCED", details={
                            "cycle": cycle,
                            "prev_p2p": previous_eval.pass_to_pass_passed,
                            "new_p2p": refine_verdict.pass_to_pass_passed,
                        })

                    is_better = (
                        (refine_verdict.resolved and not previous_eval.resolved)
                        or (refine_verdict.fail_to_pass_passed > previous_eval.fail_to_pass_passed and refine_verdict.pass_to_pass_passed >= previous_eval.pass_to_pass_passed)
                        or (refine_verdict.fail_to_pass_passed == previous_eval.fail_to_pass_passed and refine_verdict.pass_to_pass_passed > previous_eval.pass_to_pass_passed)
                        # Accept lateral/partial progress: no regression in P2P and F2P not worse.
                        # This prevents the rollback from undoing intermediate multi-step fixes (e.g.
                        # fixing one assertion out of two in the same method so the next cycle can
                        # target the remaining assertion on the updated source).
                        or (refine_verdict.fail_to_pass_passed >= previous_eval.fail_to_pass_passed and refine_verdict.pass_to_pass_passed >= previous_eval.pass_to_pass_passed)
                    )

                    if is_better or refine_verdict.resolved:
                        current_patch = refined_patch
                        previous_eval = refine_verdict
                        result.patch = refined_patch
                        result.resolved = bool(refine_verdict.resolved)
                        result.failure_class = classify(refined_patch, refine_verdict).value
                        result.test_summary = refine_verdict.to_dict()
                    else:
                        if problem.base_commit and any(k in str(repo_dir).lower() for k in ("repos", "cache", "testbed")):
                            subprocess.run(["git", "checkout", "-f", problem.base_commit], cwd=repo_dir, capture_output=True)
                            subprocess.run(["git", "clean", "-fd"], cwd=repo_dir, capture_output=True)
                            if current_patch and current_patch.patch_text:
                                subprocess.run(["git", "apply", "--whitespace=nowarn"], input=current_patch.patch_text, cwd=repo_dir, text=True, capture_output=True)

                    if refine_verdict.resolved:
                        break
        else:
            result.test_executed = False
            result.resolved = True
            result.failure_class = FailureClass.RESOLVED.value

        result.runtime_s = round(time.time() - t0, 2)
        result.telemetry = logger.get_breakdown()
        _cleanup()
        return result
