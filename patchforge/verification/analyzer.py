"""Deterministic Failure Analysis and Semantic Diagnosis for PatchForge V0.4.2.

Parses test execution traces, assertion differences, and exception structures to
provide grounded, evidence-backed diagnostics without model hallucination or gold-patch leakage.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from patchforge.core.target import RepairTarget
from patchforge.retrieval.evidence import ExecutionEvidence


@dataclass
class RegressionCluster:
    """Group of regression failures sharing common root cause / traceback locus."""
    cluster_id: str
    error_type: str
    file: str = ""
    line: int = 0
    message: str = ""
    count: int = 0
    failing_tests: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "cluster_id": self.cluster_id,
            "error_type": self.error_type,
            "file": self.file,
            "line": self.line,
            "message": self.message,
            "count": self.count,
            "failing_tests": list(self.failing_tests),
        }


@dataclass
class SemanticDiagnosis:
    """Compact structured semantic diagnosis derived strictly from test evidence."""
    failure_class: str = ""
    failed_tests: list[str] = field(default_factory=list)
    observed_behavior: str = ""
    expected_behavior: str = ""
    missing_behavior: str = ""
    affected_target: str = ""
    regression_scope: str = ""
    confidence: float = 0.0
    clusters: list[RegressionCluster] = field(default_factory=list)
    diagnostics_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "failure_class": self.failure_class,
            "failed_tests": list(self.failed_tests),
            "observed_behavior": self.observed_behavior,
            "expected_behavior": self.expected_behavior,
            "missing_behavior": self.missing_behavior,
            "affected_target": self.affected_target,
            "regression_scope": self.regression_scope,
            "confidence": self.confidence,
            "clusters": [c.to_dict() for c in self.clusters],
            "diagnostics_text": self.diagnostics_text,
        }


class FailureAnalyzer:
    """Deterministic failure analyzer extracting assertion diffs and traceback loci."""

    @staticmethod
    def extract_test_details(raw_output: str, test_name: str) -> dict[str, str]:
        """Locates failure block in test output for a specific test name."""
        details = {
            "error_type": "",
            "error_message": "",
            "assertion_diff": "",
            "target_locus": "",
            "traceback_snippet": "",
        }
        if not raw_output or not test_name:
            return details

        # Normalize test name for matching (e.g. test_requests.py::TestRedirects::test_foo -> test_foo)
        simple_name = test_name.split("::")[-1]
        pattern = re.compile(
            rf"(?:_{{2,}}\s+(?:[\w\.]+\.)?{re.escape(simple_name)}\s+_{{2,}}|FAIL(?:ED)?\s+.*?{re.escape(simple_name)})([\s\S]*?)(?=(?:_{{2,}}\s+[\w\.]+\s+_{{2,}}|\={3,}\s+short test summary info|\={3,}\s+FAILURES|\Z))",
            re.IGNORECASE,
        )
        match = pattern.search(raw_output)
        body = match.group(1) if match else ""

        if not body:
            # Fallback search if full block not cleanly bounded
            loc = raw_output.find(simple_name)
            if loc != -1:
                body = raw_output[loc : loc + 2000]

        if body:
            # Look for exception lines: E   ExceptionType: message
            e_lines = [line.strip() for line in body.splitlines() if line.strip().startswith("E ")]
            if e_lines:
                first_e = e_lines[0][2:].strip()
                colon_idx = first_e.find(":")
                if colon_idx != -1 and " " not in first_e[:colon_idx]:
                    details["error_type"] = first_e[:colon_idx].strip()
                    details["error_message"] = first_e[colon_idx + 1:].strip()
                else:
                    details["error_type"] = "AssertionError" if "assert" in first_e else "Error"
                    details["error_message"] = first_e

                diff_lines = [l[2:].strip() for l in e_lines if l.startswith("E -") or l.startswith("E +") or l.startswith("E   where")]
                if diff_lines:
                    details["assertion_diff"] = "\n".join(diff_lines[:4])
            else:
                # Regex search for general Exception: message
                exc_m = re.search(r"([A-Za-z_][A-Za-z0-9_]*Error|Exception):\s*(.*)", body)
                if exc_m:
                    details["error_type"] = exc_m.group(1).strip()
                    details["error_message"] = exc_m.group(2).strip()

            # Find reference to target or repo files in traceback
            tb_matches = re.findall(r"([a-zA-Z0-9_\-\.\/]+\.py):(\d+):\s*(.*)", body)
            if tb_matches:
                # Prefer matches in src or requests or repo files
                chosen = tb_matches[-1]
                for f, line, text in tb_matches:
                    if not f.startswith("/opt") and not "site-packages" in f and not "test_" in f:
                        chosen = (f, line, text)
                        break
                details["target_locus"] = f"{chosen[0]}:{chosen[1]}"
                details["traceback_snippet"] = f"{chosen[0]}:{chosen[1]}: {chosen[2]}"

        return details

    def cluster_regressions(
        self,
        evidence: ExecutionEvidence,
        raw_output: str,
    ) -> list[RegressionCluster]:
        """Clusters regression test failures by exception type and root locus."""
        groups: dict[str, list[tuple[str, dict[str, str]]]] = {}

        for test in evidence.failed_regression_tests:
            info = self.extract_test_details(raw_output, test)
            key = f"{info['error_type']}@{info['target_locus'] or info['error_message'][:40]}"
            groups.setdefault(key, []).append((test, info))

        clusters: list[RegressionCluster] = []
        c_idx = 1
        for key, members in groups.items():
            first_info = members[0][1]
            tests = [m[0] for m in members]
            loc_parts = first_info["target_locus"].split(":")
            f_path = loc_parts[0] if len(loc_parts) > 0 else ""
            line_no = int(loc_parts[1]) if len(loc_parts) > 1 and loc_parts[1].isdigit() else 0

            cluster = RegressionCluster(
                cluster_id=f"Cluster-{c_idx}",
                error_type=first_info["error_type"] or "UnknownError",
                file=f_path,
                line=line_no,
                message=first_info["error_message"] or "Regression failure",
                count=len(members),
                failing_tests=tests,
            )
            clusters.append(cluster)
            c_idx += 1

        return clusters

    def analyze(
        self,
        evidence: ExecutionEvidence,
        target: RepairTarget,
        original_hypothesis: str = "",
    ) -> SemanticDiagnosis:
        """Derives a structured SemanticDiagnosis from execution evidence and repair target."""
        raw_output = evidence.traceback or evidence.stdout or evidence.stderr
        failed_targets = evidence.failed_target_tests
        failed_regressions = evidence.failed_regression_tests

        clusters: list[RegressionCluster] = []
        if failed_regressions:
            clusters = self.cluster_regressions(evidence, raw_output)

        target_diagnostics: list[dict[str, str]] = []
        for test in failed_targets[:5]:
            info = self.extract_test_details(raw_output, test)
            info["test_name"] = test
            target_diagnostics.append(info)

        # Classify Failure Category according to Taxonomy
        failure_class = "SEMANTIC_MISUNDERSTANDING"
        observed = ""
        expected = ""
        missing = ""
        reg_scope = "None"
        confidence = 0.85

        if failed_targets and not failed_regressions:
            # Pure target failure: semantic near-miss or incomplete specification
            all_diffs = [d["assertion_diff"] for d in target_diagnostics if d["assertion_diff"]]
            all_errors = [d["error_type"] for d in target_diagnostics if d["error_type"]]

            if any("AssertionError" in err for err in all_errors) or all_diffs:
                failure_class = "SEMANTIC_MISUNDERSTANDING"
                observed_parts = []
                for d in target_diagnostics:
                    if d["assertion_diff"]:
                        observed_parts.append(d["assertion_diff"])
                    elif d["error_message"]:
                        observed_parts.append(f"{d['error_type']}: {d['error_message']}")
                observed = "; ".join(observed_parts[:3]) or "Target assertion failed during test execution."
                expected = "Target tests expect correct return value / exception per specification."
                missing = "The patch modified behavior but produced incorrect output or failed to update state."
            else:
                failure_class = "INCOMPLETE_SPECIFICATION"
                observed = f"{len(failed_targets)} target test(s) failed without assertion diff."
                expected = "All target test conditions satisfied."
                missing = "Certain conditions or branch requirements in the issue were not addressed."

        elif failed_regressions and not failed_targets:
            # Pure regression: blast radius or runtime exception introduced
            failure_class = "REGRESSION"
            c_summary = ", ".join(f"{c.count}x {c.error_type} ({c.message[:35]})" for c in clusters[:2])
            observed = f"Target defects repaired (100% target pass), but regressions occurred: {c_summary}."
            expected = "Target defects repaired with 0 regressions on existing test suite."
            missing = "The modification altered an existing contract or introduced an uncaught exception / runtime error."
            reg_scope = f"{len(failed_regressions)} regression failures across {len(clusters)} cluster(s)."

        elif failed_targets and failed_regressions:
            # Mixed failure: blast radius
            failure_class = "BLAST_RADIUS"
            observed = f"{len(failed_targets)} target test(s) and {len(failed_regressions)} regression test(s) failed."
            expected = "Surgical defect repair without breaking existing callers."
            missing = "Over-broad change or incorrect assumption broke surrounding invariants."
            reg_scope = f"{len(failed_regressions)} regressions."

        elif not failed_targets and not failed_regressions:
            failure_class = "RESOLVED"
            observed = "All target and regression tests passed."
            expected = "All tests pass."
            missing = "None"
            confidence = 1.0

        # Construct compact diagnostic text for the model prompt
        diag_lines = [
            "=== EXECUTION EVIDENCE ===",
            f"Target: {target.file_path}::{target.symbol}",
            f"Target tests: {evidence.target_tests_passed}/{evidence.target_tests_total} passed",
        ]

        if failed_targets:
            diag_lines.append("Failed Target Tests:")
            for d in target_diagnostics:
                diag_lines.append(f"- {d['test_name']}")
                if d["error_type"] or d["error_message"]:
                    diag_lines.append(f"  Error: {d['error_type']}: {d['error_message']}")
                if d["assertion_diff"]:
                    diag_lines.append(f"  Diff:\n    " + "\n    ".join(d["assertion_diff"].splitlines()))
                if d["target_locus"]:
                    diag_lines.append(f"  Locus: {d['target_locus']}")

        diag_lines.append(
            f"Regression tests: {evidence.regression_tests_passed}/{evidence.regression_tests_total} passed"
        )
        if clusters:
            diag_lines.append("Regression Clusters:")
            for c in clusters[:3]:
                loc_str = f" at {c.file}:{c.line}" if c.file else ""
                diag_lines.append(f"- [{c.cluster_id}] {c.count} test(s) failed with {c.error_type}{loc_str}: {c.message}")

        diag_lines.extend([
            "",
            "=== SEMANTIC DIAGNOSIS ===",
            f"Failure Category: {failure_class}",
            f"Observed Behavior: {observed}",
            f"Expected Behavior: {expected}",
            f"Missing / Defective Implementation: {missing}",
        ])
        if reg_scope != "None":
            diag_lines.append(f"Regression Scope: {reg_scope}")

        diagnostics_text = "\n".join(diag_lines)

        return SemanticDiagnosis(
            failure_class=failure_class,
            failed_tests=failed_targets + failed_regressions,
            observed_behavior=observed,
            expected_behavior=expected,
            missing_behavior=missing,
            affected_target=f"{target.file_path}::{target.symbol}",
            regression_scope=reg_scope,
            confidence=confidence,
            clusters=clusters,
            diagnostics_text=diagnostics_text,
        )
