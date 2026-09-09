"""Bounded repair loop: at most max_patch_attempts patches total.

Order: untried hypotheses first (rank order); when all are tried, one
major retry of the best hypothesis with aggregated failure feedback.
Stops early on RESOLVED. Never an unlimited agent loop.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

from patchforge.core.config import PatchForgeConfig
from patchforge.issue.problem import Problem
from patchforge.localization.candidate import Candidate
from patchforge.reasoning.hypothesis import Hypothesis
from patchforge.repair.generator import PatchGenerator
from patchforge.repair.patch import Patch
from patchforge.verification.classifier import FailureClass, classify
from patchforge.verification.tester import Tester


@dataclass
class LoopResult:
    resolved: bool = False
    attempts: list[dict] = field(default_factory=list)
    best_patch: Patch | None = None
    failure_class: str = FailureClass.UNRESOLVED.value


class RepairLoop:
    def __init__(self, config: PatchForgeConfig, generator: PatchGenerator, tester: Tester,
                 exec_adapter=None, repo_dir: str = ""):
        self.config = config
        self.generator = generator
        self.tester = tester
        self.exec_adapter = exec_adapter
        self.repo_dir = repo_dir

    def _applies_cleanly(self, patch: Patch, attempt_dir: str) -> tuple[bool, str]:
        """Read-only `git apply --check` via the execution layer (no checkout mutation)."""
        if self.exec_adapter is None or not self.repo_dir:
            return True, ""
        os.makedirs(attempt_dir, exist_ok=True)
        diff_path = os.path.join(attempt_dir, "patch.diff")
        with open(diff_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(patch.patch_text)
        from patchforge.integrations.minisweagent import MiniSWEAgentAdapter
        wsl_path = MiniSWEAgentAdapter._to_wsl(os.path.abspath(diff_path))
        # -c core.autocrlf=true mirrors the native Windows checkout behavior;
        # without it WSL git rejects LF diffs against CRLF working files that
        # both native git and the Linux eval container accept.
        res = self.exec_adapter.run_bash(
            f'git -c core.autocrlf=true apply --check "{wsl_path}"', self.repo_dir)
        if res.returncode != 0:
            return False, f"git apply --check failed: {res.output.strip()[:300]}"
        return True, ""

    def run(self, problem: Problem, hypotheses: list[Hypothesis],
            candidates: list[Candidate], workdir: str, run_id: str) -> LoopResult:
        result = LoopResult()
        tried: set[str] = set()
        feedback: list[str] = []
        attempt_no = 0
        pool = hypotheses[: self.config.max_hypotheses]
        
        retained_hyp: Hypothesis | None = None
        retained_feedback: str = ""
        best_score = -1

        retried = False
        while attempt_no < self.config.max_patch_attempts:
            if retained_hyp is not None:
                hyp = retained_hyp
                prev_error = retained_feedback
                retained_hyp = None
                retained_feedback = ""
                was_retained = True
            else:
                remaining = [h for h in pool if h.id not in tried]
                if remaining:
                    hyp = remaining[0]
                    tried.add(hyp.id)
                    prev_error = ""
                else:
                    if not pool:
                        break
                    hyp = pool[0]
                    prev_error = " | ".join(feedback[-2:]) or "previous attempt failed"
                    retried = True
                was_retained = False

            attempt_no += 1
            started = time.time()
            patch = self.generator.generate(problem, hyp, previous_error=prev_error)
            attempt_dir = f"{workdir}/attempt_{attempt_no}"
            ok, apply_err = self._applies_cleanly(patch, attempt_dir)
            if patch.valid and not ok:
                patch.valid = False
                patch.error = apply_err
            report = self.tester.test(problem.instance_id, patch, attempt_dir,
                                      f"{run_id}-a{attempt_no}")
            failure = classify(patch, report.eval, candidates) if report.eval else (
                FailureClass.PATCH_SYNTAX if not report.syntax_ok else FailureClass.INFRA_FAILURE)
            if not report.syntax_ok:
                failure = FailureClass.PATCH_SYNTAX

            ev = report.eval
            f2p_p = ev.fail_to_pass_passed if ev else 0
            f2p_t = ev.fail_to_pass_total if ev else 0
            p2p_p = ev.pass_to_pass_passed if ev else 0
            p2p_t = ev.pass_to_pass_total if ev else 0
            p2p_regr = p2p_t - p2p_p

            score = (f2p_p * 100) + p2p_p if patch.valid else -1
            if score > best_score:
                best_score = score
                result.best_patch = patch

            record = {
                "attempt": attempt_no,
                "hypothesis_id": hyp.id,
                "patch": patch.to_dict(),
                "tests": report.to_dict(),
                "failure_class": failure.value,
                "runtime_s": round(time.time() - started, 1),
                "f2p_passed": f2p_p,
                "f2p_total": f2p_t,
                "p2p_passed": p2p_p,
                "p2p_total": p2p_t,
                "p2p_regressions": p2p_regr,
                "retained": was_retained,
                "refinement": patch.is_refinement,
                "match_tier": patch.match_tier,
                "ast_repair_used": patch.ast_repair_used,
                "failure_feedback": prev_error,
            }
            result.attempts.append(record)

            if failure == FailureClass.RESOLVED:
                result.resolved = True
                result.failure_class = failure.value
                result.best_patch = patch
                return result

            result.failure_class = failure.value
            feedback.append(f"{hyp.id}: {failure.value} (F2P {f2p_p}/{f2p_t})"
                            + (f" syntax: {report.syntax_error}" if not report.syntax_ok else ""))

            # Near-miss retention policy: retain if F2P >= 50% AND 0 P2P regressions
            if (f2p_t > 0 and f2p_p >= 1 and (f2p_p / f2p_t) >= 0.50 and p2p_regr == 0
                    and attempt_no < self.config.max_patch_attempts):
                retained_hyp = hyp
                err_snippet = f" (error: {ev.error[:300]})" if ev and ev.error else ""
                retained_feedback = (
                    f"Previous patch for {hyp.id} passed {f2p_p}/{f2p_t} F2P tests and {p2p_p}/{p2p_t} P2P tests "
                    f"with 0 regressions. However, {f2p_t - f2p_p} F2P tests failed{err_snippet}. "
                    f"Refine the patch on {hyp.id} to fix the remaining failing test without breaking passing tests."
                )

            if retried and retained_hyp is None:
                break

        if result.best_patch is None and result.attempts:
            result.best_patch = patch
        return result
