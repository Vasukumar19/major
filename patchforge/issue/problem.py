"""Problem: the ONLY task data PatchForge may see.

Integrity gate: from_dataset_row() copies an allowlist of fields.
The gold patch ('patch') and hidden tests ('test_patch') can never
reach the agent through this object.
"""
from __future__ import annotations

from dataclasses import dataclass, field

FORBIDDEN_FIELDS = ("patch", "test_patch")


@dataclass
class Problem:
    instance_id: str = ""
    repo: str = ""
    base_commit: str = ""
    version: str = ""
    problem_statement: str = ""
    hints_text: str = ""
    fail_to_pass: list[str] = field(default_factory=list)
    pass_to_pass: list[str] = field(default_factory=list)

    @classmethod
    def from_dataset_row(cls, row: dict) -> "Problem":
        return cls(
            instance_id=str(row.get("instance_id", "")),
            repo=str(row.get("repo", "")),
            base_commit=str(row.get("base_commit", "")),
            version=str(row.get("version", "")),
            problem_statement=str(row.get("problem_statement", "")),
            hints_text=str(row.get("hints_text", "")),
            fail_to_pass=list(row.get("FAIL_TO_PASS") or []),
            pass_to_pass=list(row.get("PASS_TO_PASS") or []),
        )

    def to_dict(self) -> dict:
        return {
            "instance_id": self.instance_id,
            "repo": self.repo,
            "base_commit": self.base_commit,
            "version": self.version,
            "problem_statement": self.problem_statement,
            "hints_text": self.hints_text,
            "FAIL_TO_PASS": self.fail_to_pass,
            "PASS_TO_PASS": self.pass_to_pass,
        }
