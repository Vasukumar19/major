"""Authoritative RepairTarget and EditSite dataclasses (PatchForge V0.4.3).

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
class EditSite:
    """Represents an editable region in a source file."""
    file_path: str
    symbol: str = ""
    line_start: int = 1
    line_end: int = 1
    source_span: tuple[int, int] = (1, 1)
    verified_source: str = ""
    verification_status: bool = False
    behavior_context: str = ""
    site_role: str = "PRIMARY"  # PRIMARY, SECONDARY, RELATED_CALLER, RELATED_HELPER, PROTOCOL_SITE, TEST_DERIVED
    rank: int = 1

    def to_dict(self) -> dict:
        return {
            "file_path": self.file_path,
            "symbol": self.symbol,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "source_span": list(self.source_span),
            "verified_source": self.verified_source,
            "verification_status": self.verification_status,
            "behavior_context": self.behavior_context,
            "site_role": self.site_role,
            "rank": self.rank,
        }

    @classmethod
    def from_dict(cls, data: dict) -> EditSite:
        span = data.get("source_span", (data.get("line_start", 1), data.get("line_end", 1)))
        if isinstance(span, list):
            span = tuple(span)
        return cls(
            file_path=data.get("file_path", ""),
            symbol=data.get("symbol", ""),
            line_start=data.get("line_start", 1),
            line_end=data.get("line_end", 1),
            source_span=span,
            verified_source=data.get("verified_source", ""),
            verification_status=data.get("verification_status", False),
            behavior_context=data.get("behavior_context", ""),
            site_role=data.get("site_role", "PRIMARY"),
            rank=data.get("rank", 1),
        )



@dataclass
class RepairTarget:
    """Authoritative repair target specification.
    
    Subsystems (controller, context builder, patch generator, editor, policy, model)
    must NOT independently decide or alter where the patch applies.
    """
    repository: str = ""
    file_path: str = ""
    symbol: str = ""
    line_start: int = 1
    line_end: int = 1
    source_span: tuple[int, int] = (1, 1)
    verified_source: str = ""
    verification_status: bool = False
    context_lines: list[str] = field(default_factory=list)
    behavior_context: str = ""
    secondary_sites: list[EditSite] = field(default_factory=list)

    @property
    def primary_site(self) -> EditSite:
        return EditSite(
            file_path=self.file_path,
            symbol=self.symbol,
            line_start=self.line_start,
            line_end=self.line_end,
            source_span=self.source_span,
            verified_source=self.verified_source,
            verification_status=self.verification_status,
            behavior_context=self.behavior_context,
        )

    def all_sites(self) -> list[EditSite]:
        return [self.primary_site, *self.secondary_sites]

    def has_secondary_sites(self) -> bool:
        return len(self.secondary_sites) > 0

    def add_secondary_site(self, site: EditSite) -> bool:
        norm_file = site.file_path.replace("\\", "/").strip().lstrip("/")
        for existing in self.all_sites():
            if existing.file_path.replace("\\", "/").strip().lstrip("/") == norm_file and existing.symbol == site.symbol:
                return False
        self.secondary_sites.append(site)
        return True

    def is_grounded(self, search_block: str) -> bool:
        """Verify that a candidate SEARCH block exists verbatim in verified sources."""
        if not search_block:
            return False
        clean_search = search_block.strip()
        if not clean_search:
            return False
        for s in self.all_sites():
            if s.verified_source and clean_search in s.verified_source:
                return True
        return False

    def check_file_match(self, proposed_file_path: str) -> bool:
        """Check if proposed file path strictly matches any editable target site."""
        if not proposed_file_path:
            return False
        norm_proposed = proposed_file_path.replace("\\", "/").strip().lstrip("/")
        for s in self.all_sites():
            if s.file_path.replace("\\", "/").strip().lstrip("/") == norm_proposed:
                return True
        return False

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
            "behavior_context": self.behavior_context,
            "secondary_sites": [s.to_dict() for s in self.secondary_sites],
        }

    @classmethod
    def from_dict(cls, data: dict) -> RepairTarget:
        span = data.get("source_span", (data.get("line_start", 1), data.get("line_end", 1)))
        if isinstance(span, list):
            span = tuple(span)
        sec_sites = [EditSite.from_dict(d) for d in data.get("secondary_sites", [])]
        return cls(
            repository=data.get("repository", ""),
            file_path=data.get("file_path", ""),
            symbol=data.get("symbol", ""),
            line_start=data.get("line_start", 1),
            line_end=data.get("line_end", 1),
            source_span=span,
            verified_source=data.get("verified_source", ""),
            verification_status=data.get("verification_status", False),
            context_lines=data.get("context_lines", []),
            behavior_context=data.get("behavior_context", ""),
            secondary_sites=sec_sites,
        )


MultiSiteRepairTarget = RepairTarget
