"""Every localization/hypothesis decision must be traceable to evidence."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class EvidenceType(str, Enum):
    ISSUE_TEXT = "ISSUE_TEXT"
    SYMBOL_MATCH = "SYMBOL_MATCH"
    SEMANTIC_MATCH = "SEMANTIC_MATCH"
    GRAPH_RELATION = "GRAPH_RELATION"
    IMPORT_RELATION = "IMPORT_RELATION"
    CALL_RELATION = "CALL_RELATION"
    TEST_REFERENCE = "TEST_REFERENCE"
    STACK_TRACE = "STACK_TRACE"
    ERROR_MESSAGE = "ERROR_MESSAGE"
    CODE_BEHAVIOR = "CODE_BEHAVIOR"
    GIT_HISTORY = "GIT_HISTORY"
    STATIC_REFERENCE = "STATIC_REFERENCE"
    IDENTIFIER_MATCH = "IDENTIFIER_MATCH"
    TEST_FAILURE_EVIDENCE = "TEST_FAILURE_EVIDENCE"
    TRACEBACK_EVIDENCE = "TRACEBACK_EVIDENCE"
    ASSERTION_DIFF = "ASSERTION_DIFF"


@dataclass
class Evidence:
    source: str = ""
    type: str = EvidenceType.ISSUE_TEXT.value
    file: str = ""
    symbol: str = ""
    line: int = 0
    relevance: float = 0.0
    explanation: str = ""

    def __post_init__(self):
        allowed = {t.value for t in EvidenceType}
        if self.type not in allowed:
            raise ValueError(f"Unknown evidence type: {self.type}")
        self.relevance = max(0.0, min(1.0, float(self.relevance)))

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "type": self.type,
            "file": self.file,
            "symbol": self.symbol,
            "line": self.line,
            "relevance": self.relevance,
            "explanation": self.explanation,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Evidence":
        return cls(
            source=d.get("source", ""),
            type=d.get("type", EvidenceType.ISSUE_TEXT.value),
            file=d.get("file", ""),
            symbol=d.get("symbol", ""),
            line=int(d.get("line", 0) or 0),
            relevance=float(d.get("relevance", 0.0) or 0.0),
            explanation=d.get("explanation", ""),
        )


@dataclass
class ExecutionEvidence:
    """Structured evidence captured from test harness execution."""
    task_id: str = ""
    target_tests_total: int = 0
    target_tests_passed: int = 0
    target_tests_failed: int = 0
    regression_tests_total: int = 0
    regression_tests_passed: int = 0
    regression_tests_failed: int = 0
    failure_class: str = ""
    failed_target_tests: list[str] = field(default_factory=list)
    failed_regression_tests: list[str] = field(default_factory=list)
    passed_target_tests: list[str] = field(default_factory=list)
    passed_regression_tests: list[str] = field(default_factory=list)
    failure_messages: list[str] = field(default_factory=list)
    traceback: str = ""
    stdout: str = ""
    stderr: str = ""
    patch: str = ""

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "target_tests_total": self.target_tests_total,
            "target_tests_passed": self.target_tests_passed,
            "target_tests_failed": self.target_tests_failed,
            "regression_tests_total": self.regression_tests_total,
            "regression_tests_passed": self.regression_tests_passed,
            "regression_tests_failed": self.regression_tests_failed,
            "failure_class": self.failure_class,
            "failed_target_tests": list(self.failed_target_tests),
            "failed_regression_tests": list(self.failed_regression_tests),
            "passed_target_tests": list(self.passed_target_tests),
            "passed_regression_tests": list(self.passed_regression_tests),
            "failure_messages": list(self.failure_messages),
            "traceback": self.traceback,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "patch": self.patch,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ExecutionEvidence":
        return cls(
            task_id=d.get("task_id", ""),
            target_tests_total=int(d.get("target_tests_total", 0) or 0),
            target_tests_passed=int(d.get("target_tests_passed", 0) or 0),
            target_tests_failed=int(d.get("target_tests_failed", 0) or 0),
            regression_tests_total=int(d.get("regression_tests_total", 0) or 0),
            regression_tests_passed=int(d.get("regression_tests_passed", 0) or 0),
            regression_tests_failed=int(d.get("regression_tests_failed", 0) or 0),
            failure_class=d.get("failure_class", ""),
            failed_target_tests=list(d.get("failed_target_tests", []) or []),
            failed_regression_tests=list(d.get("failed_regression_tests", []) or []),
            passed_target_tests=list(d.get("passed_target_tests", []) or []),
            passed_regression_tests=list(d.get("passed_regression_tests", []) or []),
            failure_messages=list(d.get("failure_messages", []) or []),
            traceback=d.get("traceback", ""),
            stdout=d.get("stdout", ""),
            stderr=d.get("stderr", ""),
            patch=d.get("patch", ""),
        )
