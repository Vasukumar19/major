"""Common Normalized Input Boundary for PatchForge AI.

Accepts work items from external applications (GitHub webhooks/APIs,
CI/CD systems, dataset rows, or direct REST submissions) and normalizes
them into a validated PatchForgeWorkItem before entering the repair pipeline.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from patchforge.issue.problem import Problem


@dataclass
class PatchForgeWorkItem:
    """Normalized unit of repair work ingested from any external or internal source."""
    source_application: str  # "github", "swebench", "direct_api", etc.
    source_event_id: str  # Delivery ID, event UUID, or dataset instance ID
    repository: str  # e.g. "psf/requests" or local path
    problem_statement: str  # Defect description or issue content
    issue_id: str = ""  # Issue number or task identifier
    title: str = ""
    base_commit: str = ""  # Base SHA or branch
    reproduction_test: str = ""  # Targeted test name or invocation command
    hints_text: str = ""
    labels: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    raw_payload: Optional[Dict[str, Any]] = None  # Safe copy for provenance/debugging
    correlation_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def validate(self) -> Tuple[bool, str]:
        """Validates that all essential fields exist and are syntactically well-formed."""
        if not self.source_application.strip():
            return False, "Validation error: 'source_application' must not be empty."
        if not self.source_event_id.strip():
            return False, "Validation error: 'source_event_id' must not be empty."
        if not self.repository.strip():
            return False, "Validation error: 'repository' must not be empty."
        if not self.problem_statement.strip() and not self.title.strip():
            return False, "Validation error: Work item must provide a non-empty title or problem statement."

        # Security check: disallow parent directory escapes in repository identifier
        if ".." in self.repository:
            return False, "Validation error: 'repository' contains forbidden directory traversal characters ('..')."

        return True, ""

    def to_problem(self) -> Problem:
        """Converts normalized work item into internal Problem object."""
        # Compose full problem statement
        full_statement = self.problem_statement
        if self.title and self.title not in full_statement:
            full_statement = f"{self.title}\n\n{full_statement}".strip()

        fail_to_pass = [self.reproduction_test] if self.reproduction_test.strip() else []

        return Problem(
            instance_id=self.issue_id or self.source_event_id or f"{self.source_application}-{self.correlation_id[:8]}",
            repo=self.repository,
            base_commit=self.base_commit,
            version=str(self.metadata.get("version", "")),
            problem_statement=full_statement,
            hints_text=self.hints_text,
            fail_to_pass=fail_to_pass,
            pass_to_pass=list(self.metadata.get("pass_to_pass", []) or []),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_application": self.source_application,
            "source_event_id": self.source_event_id,
            "correlation_id": self.correlation_id,
            "repository": self.repository,
            "issue_id": self.issue_id,
            "title": self.title,
            "problem_statement": self.problem_statement,
            "base_commit": self.base_commit,
            "reproduction_test": self.reproduction_test,
            "hints_text": self.hints_text,
            "labels": self.labels,
            "metadata": self.metadata,
        }
