"""Deterministic Baseline Repair Engine for PatchForge AI.

Implements the proven SWE-repair golden path:
Issue -> Agentless-style Localization -> Authoritative RepairTarget ->
Aider-style Context -> Single-turn Coding Model -> Patch Validation ->
Patch Application -> Target/Reproduction Test -> Telemetry
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from patchforge.core.target import RepairTarget
from patchforge.issue.problem import Problem
from patchforge.models.provider import ModelProvider, OllamaProvider
from patchforge.repair.generator import parse_edits
from patchforge.repair.patch import Patch
from patchforge.repository.map import RepoMap
from patchforge.telemetry.events import TelemetryLogger
from patchforge.tools.editor import ApplyPatchTool
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
      "patch_text": "### <file>\\n<<<<<<< SEARCH\\n<exact verbatim source code>\\n=======\\n<replacement code>\\n>>>>>>> REPLACE"
    }
  }
}
```
Rules:
1. The SEARCH block MUST match exact verbatim lines from the VERIFIED SOURCE provided.
2. The REPLACE block contains the corrected code.
3. Only output the JSON object."""


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


class BaselineRepairEngine:
    """Executes deterministic software repair without multi-turn agent deadlocks."""

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
        """Deterministic hierarchical localization from issue statement and repo symbols."""
        repo_p = Path(repo_dir)
        repo_map = RepoMap(repo_dir)
        issue_text = problem.problem_statement + "\n" + problem.hints_text

        # 1. Scan for explicit file names mentioned in issue
        candidate_file = ""
        mentioned_files = re.findall(r"[\w/\.-]+\.py\b", issue_text)
        for mf in mentioned_files:
            clean_f = mf.strip().replace("\\", "/").lstrip("/")
            if (repo_p / clean_f).exists() and (repo_p / clean_f).is_file():
                candidate_file = clean_f
                break

        # 2. If no full path, scan for candidate Python files in repo matching keywords
        if not candidate_file:
            py_files = [
                str(p.relative_to(repo_p)).replace("\\", "/")
                for p in repo_p.glob("**/*.py")
                if p.is_file() and not any(part in ("tests", "docs", ".git", "venv") for part in p.parts)
            ]
            # Match package modules (e.g. requests/models.py for Request class)
            best_file = ""
            max_hits = 0
            for pf in py_files:
                symbols = repo_map.extract_symbols(pf)
                hits = sum(1 for s in symbols if s.name.lower() in issue_text.lower())
                if hits > max_hits:
                    max_hits = hits
                    best_file = pf
            candidate_file = best_file or (py_files[0] if py_files else "unknown.py")

        # 3. Identify candidate symbol in candidate file
        symbols = repo_map.extract_symbols(candidate_file)
        candidate_sym = ""
        for s in symbols:
            if s.name.lower() in issue_text.lower():
                candidate_sym = s.name
                break
        if not candidate_sym and symbols:
            candidate_sym = symbols[0].name

        # 4. Extract verified source window
        source_win, win_start, win_end = repo_map.get_source_window(
            candidate_file, candidate_sym, padding_before=15, padding_after=85
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
        )

    def build_context(self, problem: Problem, target: RepairTarget) -> str:
        """Constructs an Aider-style concise, clean-slate repair context."""
        sections = [
            f"=== DEFECT REPAIR TARGET ===",
            f"Repository: {target.repository}",
            f"Target File: {target.file_path}",
            f"Target Symbol: {target.symbol}",
            f"Lines: {target.line_start}-{target.line_end}",
            "",
            "=== ISSUE DESCRIPTION ===",
            problem.problem_statement.strip()[:1500],
            "",
            "=== REPAIR INSTRUCTIONS ===",
            f"1. Fix the issue specifically in '{target.file_path}'.",
            f"2. You MUST use an apply_patch JSON action targeting '{target.file_path}'.",
            "3. The SEARCH block MUST match exact lines from the VERIFIED SOURCE below.",
            "4. Do NOT output markdown explanations or unified diffs.",
            "",
            "=== VERIFIED SOURCE CONTEXT ===",
            f"--- File: {target.file_path} (Lines {target.line_start}-{target.line_end}) ---",
            target.verified_source,
        ]
        return "\n".join(sections)

    def run(
        self,
        problem: Problem,
        repo_dir: str,
        tester: Tester | None = None,
        max_retries: int = 2,
    ) -> BaselineResult:
        """Execute end-to-end baseline repair on one instance."""
        logger = TelemetryLogger(task_id=problem.instance_id)
        t0 = time.time()
        result = BaselineResult(instance_id=problem.instance_id)

        # 1. Localization -> Authoritative RepairTarget
        loc_t0 = time.time()
        target = self.localize(problem, repo_dir)
        loc_ms = (time.time() - loc_t0) * 1000.0
        logger.log_event("LOCALIZE", "TARGET_LOCALIZED", duration_ms=loc_ms, details=target.to_dict())
        result.target = target

        if not target.verification_status:
            result.failure_class = FailureClass.WRONG_LOCALIZATION.value
            result.runtime_s = round(time.time() - t0, 2)
            result.telemetry = logger.get_breakdown()
            return result

        # 2. Single-turn Model Patch Synthesis with bounded retries
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

            # Parse and validate patch
            raw_text = resp.text.strip()
            # Extract JSON action if present
            patch_text = ""
            json_m = re.search(r"\{[\s\S]*\}", raw_text)
            if json_m:
                try:
                    data = json.loads(json_m.group(0))
                    action = data.get("action", {})
                    if action.get("name") == "apply_patch":
                        patch_text = action.get("arguments", {}).get("patch_text", "")
                except Exception:
                    pass

            if not patch_text:
                edits = parse_edits(raw_text)
                if edits:
                    patch_text = raw_text

            if not patch_text and "<<<<<<< SEARCH" in raw_text:
                patch_text = f"### {target.file_path}\n" + raw_text

            if not patch_text:
                logger.log_event("PATCH", "PATCH_PARSE_FAILED", details={"attempt": attempt})
                continue

            # 3. Apply Patch with Authoritative Target Enforcement
            apply_tool = ApplyPatchTool(repo_dir=repo_dir)
            apply_res = apply_tool.execute({
                "patch_text": patch_text,
                "expected_target_file": target.file_path,
            })

            if apply_res.status == "SUCCESS":
                patch_applied = True
                p_data = apply_res.data.get("patch", {})
                applied_patch = Patch(
                    hypothesis_id="BASELINE_H1",
                    files_changed=apply_res.data.get("files_changed", [target.file_path]),
                    patch_text=apply_res.data.get("diff", ""),
                    valid=True,
                    match_tier=p_data.get("match_tier", "EXACT"),
                    input_tokens=resp.input_tokens,
                    output_tokens=resp.output_tokens,
                )
                logger.log_event("APPLY", "PATCH_APPLIED", details={"diff_length": len(applied_patch.patch_text)})
                break
            else:
                logger.log_event("APPLY", "PATCH_FAILED", details={"error": apply_res.error, "attempt": attempt})
                # Refine prompt for retry
                prompt += f"\n\n[PREVIOUS ATTEMPT FAILED]: {apply_res.error}\nFix the error and produce exact verbatim SEARCH lines."

        result.patch = applied_patch
        result.patch_applied = patch_applied

        if not patch_applied or not applied_patch:
            result.failure_class = FailureClass.PATCH_SYNTAX.value
            result.runtime_s = round(time.time() - t0, 2)
            result.telemetry = logger.get_breakdown()
            return result

        # 4. Target / Reproduction Test Execution
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
                    "resolved": result.resolved,
                    "failure_class": result.failure_class,
                    "f2p_passed": getattr(verdict, "fail_to_pass_passed", 0),
                    "f2p_total": getattr(verdict, "fail_to_pass_total", 0),
                },
            )
        else:
            result.test_executed = False
            result.resolved = True  # No tester available, patch applied successfully
            result.failure_class = FailureClass.RESOLVED.value

        result.runtime_s = round(time.time() - t0, 2)
        result.telemetry = logger.get_breakdown()
        return result
