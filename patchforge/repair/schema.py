"""Authoritative data structures and schema for PatchForge AST-grounded Structured Repair Engine.

Defines:
- RepairUnitType & RepairAction enums
- RepairUnit with AST node grounding and verified source spans
- StructuredRepairOutput (model-facing contract)
- ReconstructedPatch (deterministic patch from source reconstruction)
- StaticValidationResult (pre-test verification gate)
- RankedRepairSite (evidence-scored candidate site)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


class RepairUnitType(str, Enum):
    EXPRESSION = "EXPRESSION"
    STATEMENT = "STATEMENT"
    STATEMENT_BLOCK = "STATEMENT_BLOCK"
    FUNCTION = "FUNCTION"
    METHOD = "METHOD"
    CLASS_MEMBER = "CLASS_MEMBER"
    IMPORT_BLOCK = "IMPORT_BLOCK"
    DECORATOR = "DECORATOR"
    MULTI_SITE = "MULTI_SITE"


class RepairAction(str, Enum):
    REPLACE_EXPRESSION = "replace_expression"
    REPLACE_STATEMENT = "replace_statement"
    REPLACE_BLOCK = "replace_block"
    REPLACE_FUNCTION_BODY = "replace_function_body"
    REPLACE_METHOD = "replace_method"
    INSERT_STATEMENT = "insert_statement"
    DELETE_STATEMENT = "delete_statement"
    REPLACE_DECORATOR = "replace_decorator"
    MODIFY_IMPORT = "modify_import"
    MULTI_SITE = "multi_site"


@dataclass
class RepairUnit:
    """A grounded, AST-verified code unit designated for repair."""
    id: str
    file_path: str
    symbol: str
    unit_type: RepairUnitType
    node_type: str  # e.g., "Call", "If", "Assign", "Return", "FunctionDef"
    start_line: int  # 1-indexed
    end_line: int    # 1-indexed
    start_col: int = 0
    end_col: int = 0
    source_text: str = ""
    parent_symbol: str = ""
    indentation: str = ""
    role: str = "PRIMARY"  # PRIMARY, CALL_SITE, HELPER, PROTOCOL_SITE
    rank_score: float = 1.0
    graph_evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "file_path": self.file_path,
            "symbol": self.symbol,
            "unit_type": self.unit_type.value if isinstance(self.unit_type, RepairUnitType) else str(self.unit_type),
            "node_type": self.node_type,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "start_col": self.start_col,
            "end_col": self.end_col,
            "source_text": self.source_text,
            "parent_symbol": self.parent_symbol,
            "indentation": self.indentation,
            "role": self.role,
            "rank_score": self.rank_score,
            "graph_evidence": self.graph_evidence,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RepairUnit:
        unit_type = data.get("unit_type", RepairUnitType.STATEMENT)
        if isinstance(unit_type, str):
            try:
                unit_type = RepairUnitType(unit_type)
            except ValueError:
                unit_type = RepairUnitType.STATEMENT
        return cls(
            id=data.get("id", ""),
            file_path=data.get("file_path", ""),
            symbol=data.get("symbol", ""),
            unit_type=unit_type,
            node_type=data.get("node_type", ""),
            start_line=data.get("start_line", 1),
            end_line=data.get("end_line", 1),
            start_col=data.get("start_col", 0),
            end_col=data.get("end_col", 0),
            source_text=data.get("source_text", ""),
            parent_symbol=data.get("parent_symbol", ""),
            indentation=data.get("indentation", ""),
            role=data.get("role", "PRIMARY"),
            rank_score=data.get("rank_score", 1.0),
            graph_evidence=data.get("graph_evidence", {}),
        )


@dataclass
class RankedRepairSite:
    """A candidate repair site evaluated and scored by the ranking engine."""
    file_path: str
    symbol: str
    role: str  # CALL_SITE, PRIMARY_FUNCTION, HELPER, SHARED_UTILITY
    score: float
    reasons: list[str] = field(default_factory=list)
    line_start: int = 1
    line_end: int = 1
    unrelated_callers_count: int = 0
    in_failing_traceback: bool = False
    semantic_radius: int = 1
    ast_node_type: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_path": self.file_path,
            "symbol": self.symbol,
            "role": self.role,
            "score": self.score,
            "reasons": self.reasons,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "unrelated_callers_count": self.unrelated_callers_count,
            "in_failing_traceback": self.in_failing_traceback,
            "semantic_radius": self.semantic_radius,
            "ast_node_type": self.ast_node_type,
        }


@dataclass
class StructuredRepairOutput:
    """Model-facing structured repair contract."""
    repair_action: str
    target_unit_id: str
    replacement: str
    reasoning: str = ""
    invariant: str = ""
    target_symbol: str = ""
    confidence: float = 1.0
    sub_edits: list[dict[str, Any]] = field(default_factory=list)  # For multi-site edits

    def to_dict(self) -> dict[str, Any]:
        return {
            "repair_action": self.repair_action,
            "target_unit_id": self.target_unit_id,
            "replacement": self.replacement,
            "reasoning": self.reasoning,
            "invariant": self.invariant,
            "target_symbol": self.target_symbol,
            "confidence": self.confidence,
            "sub_edits": self.sub_edits,
        }


@dataclass
class StaticValidationResult:
    """Verification results from static analysis prior to test execution."""
    valid: bool = True
    syntax_ok: bool = True
    ast_ok: bool = True
    target_changed: bool = True
    unrelated_targets_preserved: bool = True
    no_markdown_leakage: bool = True
    line_expansion_ok: bool = True
    lines_added: int = 0
    lines_deleted: int = 0
    symbols_affected: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "syntax_ok": self.syntax_ok,
            "ast_ok": self.ast_ok,
            "target_changed": self.target_changed,
            "unrelated_targets_preserved": self.unrelated_targets_preserved,
            "no_markdown_leakage": self.no_markdown_leakage,
            "line_expansion_ok": self.line_expansion_ok,
            "lines_added": self.lines_added,
            "lines_deleted": self.lines_deleted,
            "symbols_affected": self.symbols_affected,
            "errors": self.errors,
        }


@dataclass
class ReconstructedPatch:
    """The result of applying structured repair into original source."""
    success: bool
    files_changed: list[str] = field(default_factory=list)
    symbols_changed: list[str] = field(default_factory=list)
    patch_text: str = ""  # Deterministic unified diff
    modified_contents: dict[str, str] = field(default_factory=dict)
    units_applied: list[RepairUnit] = field(default_factory=list)
    validation: StaticValidationResult = field(default_factory=StaticValidationResult)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "files_changed": self.files_changed,
            "symbols_changed": self.symbols_changed,
            "patch_text": self.patch_text,
            "modified_contents": {k: len(v) for k, v in self.modified_contents.items()},
            "units_applied": [u.to_dict() for u in self.units_applied],
            "validation": self.validation.to_dict(),
            "error": self.error,
        }
