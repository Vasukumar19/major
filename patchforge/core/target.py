"""Authoritative RepairTarget dataclass.

Single source of truth across all PatchForge AI subsystems:
- localization
- context compaction
- patch synthesis
- patch validation
- patch application
- test execution
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class RepairTarget:
    """Authoritative repair target specification.
    
    Subsystems (controller, context builder, patch generator, editor, policy, model)
    must NOT independently decide or alter where the patch applies.
    """
    repository: str
    file_path: str
    symbol: str = ""
    line_start: int = 1
    line_end: int = 1
    source_span: tuple[int, int] = (1, 1)
    verified_source: str = ""
    verification_status: bool = False
    context_lines: list[str] = field(default_factory=list)

    def is_grounded(self, search_block: str) -> bool:
        """Verify that a candidate SEARCH block exists verbatim in the verified source."""
        if not search_block:
            return False
        clean_search = search_block.strip()
        if not clean_search:
            return False
        if self.verified_source and clean_search in self.verified_source:
            return True
        return False

    def check_file_match(self, proposed_file_path: str) -> bool:
        """Check if proposed file path strictly matches this target."""
        if not proposed_file_path:
            return False
        norm_proposed = proposed_file_path.replace("\\", "/").strip().lstrip("/")
        norm_target = self.file_path.replace("\\", "/").strip().lstrip("/")
        return norm_proposed == norm_target

    def to_dict(self) -> dict:
        return {
            "repository": self.repository,
            "file_path": self.file_path,
            "symbol": self.symbol,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "source_span": list(self.source_span),
            "verification_status": self.verification_status,
            "verified_source_length": len(self.verified_source),
        }
