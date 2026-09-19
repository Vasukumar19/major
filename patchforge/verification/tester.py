"""Two-stage testing: cheap local syntax check first, official
SWE-bench evaluation second. Targeted tests come from the harness
(FAIL_TO_PASS), so every official eval IS the targeted-then-broad run.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass

from patchforge.integrations.swebench import EvalResult, SWEBenchAdapter
from patchforge.repair.patch import Patch


@dataclass
class TestReport:
    syntax_ok: bool = True
    syntax_error: str = ""
    eval: EvalResult | None = None

    def to_dict(self) -> dict:
        return {
            "syntax_ok": self.syntax_ok,
            "syntax_error": self.syntax_error,
            "eval": self.eval.to_dict() if self.eval else None,
        }


class Tester:
    def __init__(self, *args, **kwargs):
        if len(args) == 2 and hasattr(args[1], "evaluate"):
            self.config = args[0]
            self.swebench = args[1]
            self.model_name = kwargs.get("model_name", "patchforge")
        elif len(args) >= 1 and hasattr(args[0], "evaluate"):
            self.swebench = args[0]
            self.model_name = args[1] if len(args) > 1 else kwargs.get("model_name", "patchforge")
        else:
            self.swebench = kwargs.get("swebench")
            self.model_name = kwargs.get("model_name", "patchforge")

    def syntax_check(self, patch: Patch) -> tuple[bool, str]:
        if not patch.valid or not patch.patch_text.strip():
            return False, patch.error or "empty patch"
        for file, content in (patch.new_contents or {}).items():
            if not file.endswith(".py"):
                continue
            try:
                ast.parse(content)
            except SyntaxError as e:
                return False, f"{file}: {e}"
        return True, ""

    def official_eval(self, instance_id: str, patch: Patch, workdir: str, run_id: str,
                      timeout: int = 300) -> EvalResult:
        preds = self.swebench.write_predictions(
            instance_id, patch.patch_text, self.model_name,
            workdir + "/predictions.json")
        return self.swebench.evaluate(preds, instance_id, run_id, timeout=timeout)

    def test(self, instance_id: str, patch: Patch, workdir: str, run_id: str) -> TestReport:
        ok, err = self.syntax_check(patch)
        if not ok:
            return TestReport(syntax_ok=False, syntax_error=err)
        return TestReport(syntax_ok=True,
                          eval=self.official_eval(instance_id, patch, workdir, run_id))

    def run(self, instance_id: str, patch_text: str, run_id: str = "", timeout: int = 300) -> EvalResult:
        import os
        import tempfile
        import time
        if not run_id or run_id == "eval":
            run_id = f"eval_{int(time.time())}"
        temp_dir = tempfile.mkdtemp(prefix="patchforge_eval_")
        preds_path = os.path.join(temp_dir, "predictions.json")
        preds = self.swebench.write_predictions(
            instance_id, patch_text, self.model_name, preds_path
        )
        return self.swebench.evaluate(preds, instance_id, run_id=run_id, timeout=timeout)
