"""Schema definitions for PatchForge Repository Intelligence & Graph Reasoning Engine."""
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set


class NodeKind(str, Enum):
    REPOSITORY = "REPOSITORY"
    PACKAGE = "PACKAGE"
    MODULE = "MODULE"
    CLASS = "CLASS"
    FUNCTION = "FUNCTION"
    METHOD = "METHOD"
    VARIABLE = "VARIABLE"
    IMPORT = "IMPORT"
    TEST = "TEST"
    TEST_CLASS = "TEST_CLASS"
    TEST_FUNCTION = "TEST_FUNCTION"
    EXCEPTION = "EXCEPTION"
    CONFIGURATION = "CONFIGURATION"


class EdgeKind(str, Enum):
    DEFINES = "DEFINES"
    CALLS = "CALLS"
    CALLED_BY = "CALLED_BY"
    IMPORTS = "IMPORTS"
    IMPORTED_BY = "IMPORTED_BY"
    INHERITS = "INHERITS"
    OVERRIDES = "OVERRIDES"
    REFERENCES = "REFERENCES"
    REFERENCED_BY = "REFERENCED_BY"
    USES = "USES"
    RETURNS = "RETURNS"
    RAISES = "RAISES"
    CATCHES = "CATCHES"
    TESTS = "TESTS"
    TESTED_BY = "TESTED_BY"
    DECORATES = "DECORATES"
    REGISTERS = "REGISTERS"
    DISPATCHES_TO = "DISPATCHES_TO"
    IMPLEMENTS = "IMPLEMENTS"


# Inverse relationship mapping for bidirectional consistency
INVERSE_EDGES = {
    EdgeKind.DEFINES: EdgeKind.DEFINES,  # Parent -> Child
    EdgeKind.CALLS: EdgeKind.CALLED_BY,
    EdgeKind.CALLED_BY: EdgeKind.CALLS,
    EdgeKind.IMPORTS: EdgeKind.IMPORTED_BY,
    EdgeKind.IMPORTED_BY: EdgeKind.IMPORTS,
    EdgeKind.REFERENCES: EdgeKind.REFERENCED_BY,
    EdgeKind.REFERENCED_BY: EdgeKind.REFERENCES,
    EdgeKind.TESTS: EdgeKind.TESTED_BY,
    EdgeKind.TESTED_BY: EdgeKind.TESTS,
}


@dataclass
class GraphNode:
    id: str
    name: str
    kind: NodeKind
    file_path: str
    start_line: int = 0
    end_line: int = 0
    docstring: Optional[str] = None
    signature: Optional[str] = None
    attributes: Dict[str, Any] = field(default_factory=dict)

    def __hash__(self):
        return hash(self.id)

    def __eq__(self, other):
        if isinstance(other, GraphNode):
            return self.id == other.id
        return False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind.value,
            "file_path": self.file_path,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "docstring": self.docstring,
            "signature": self.signature,
            "attributes": self.attributes,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GraphNode":
        return cls(
            id=data["id"],
            name=data["name"],
            kind=NodeKind(data["kind"]),
            file_path=data["file_path"],
            start_line=data.get("start_line", 0),
            end_line=data.get("end_line", 0),
            docstring=data.get("docstring"),
            signature=data.get("signature"),
            attributes=data.get("attributes", {}),
        )


@dataclass
class GraphEdge:
    source_id: str
    target_id: str
    kind: EdgeKind
    attributes: Dict[str, Any] = field(default_factory=dict)

    def __hash__(self):
        return hash((self.source_id, self.target_id, self.kind))

    def __eq__(self, other):
        if isinstance(other, GraphEdge):
            return (self.source_id, self.target_id, self.kind) == (other.source_id, other.target_id, other.kind)
        return False


    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_id": self.source_id,
            "target_id": self.target_id,
            "kind": self.kind.value,
            "attributes": self.attributes,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GraphEdge":
        return cls(
            source_id=data["source_id"],
            target_id=data["target_id"],
            kind=EdgeKind(data["kind"]),
            attributes=data.get("attributes", {}),
        )
