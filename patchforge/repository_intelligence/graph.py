"""Queryable structural repository graph built on NetworkX."""
import json
import logging
import os
from collections import defaultdict
from typing import Any, Dict, List, Optional, Set, Tuple

import networkx as nx

from patchforge.repository_intelligence.schema import (
    EdgeKind,
    GraphEdge,
    GraphNode,
    INVERSE_EDGES,
    NodeKind,
)

logger = logging.getLogger(__name__)


class RepositoryGraph:
    """Multi-relational graph of repository entities and structural/behavioral dependencies."""

    def __init__(self, repo_name: str = ""):
        self.repo_name = repo_name
        self.g = nx.MultiDiGraph()
        self.nodes: Dict[str, GraphNode] = {}
        
        # Fast lookup indices
        self.name_to_node_ids: Dict[str, Set[str]] = defaultdict(set)
        self.file_to_node_ids: Dict[str, Set[str]] = defaultdict(set)
        self.kind_to_node_ids: Dict[NodeKind, Set[str]] = defaultdict(set)

    def add_node(self, node: GraphNode):
        self.nodes[node.id] = node
        self.g.add_node(node.id, **node.to_dict())
        
        self.name_to_node_ids[node.name].add(node.id)
        # Also index by qualified name if applicable (e.g. Class.method)
        if "." in node.id:
            qualname = node.id.split(":")[-1]
            self.name_to_node_ids[qualname].add(node.id)
            
        self.file_to_node_ids[node.file_path].add(node.id)
        self.kind_to_node_ids[node.kind].add(node.id)

    def add_edge(self, edge: GraphEdge):
        # Add forward edge
        self.g.add_edge(edge.source_id, edge.target_id, key=edge.kind.value, kind=edge.kind.value, **edge.attributes)
        
        # If inverse exists, add inverse edge for fast bi-directional querying
        if edge.kind in INVERSE_EDGES:
            inv_kind = INVERSE_EDGES[edge.kind]
            if inv_kind != edge.kind:
                self.g.add_edge(edge.target_id, edge.source_id, key=inv_kind.value, kind=inv_kind.value, **edge.attributes)

    def resolve_symbol(self, symbol: str) -> List[GraphNode]:
        """Resolves a symbol name (or ID or file:symbol) to matching GraphNode instances."""
        if symbol in self.nodes:
            return [self.nodes[symbol]]
            
        matched_ids = self.name_to_node_ids.get(symbol, set())
        if matched_ids:
            return [self.nodes[nid] for nid in matched_ids if nid in self.nodes]
            
        # Try suffix or partial match
        results = []
        for nid, node in self.nodes.items():
            if nid.endswith(f":{symbol}") or nid.endswith(f".{symbol}"):
                results.append(node)
        return results

    def callers(self, symbol: str) -> List[GraphNode]:
        """Finds functions/methods that CALL the given symbol."""
        callers = []
        nodes = self.resolve_symbol(symbol)
        visited = set()
        
        for node in nodes:
            # Look for incoming CALLS edges or outgoing CALLED_BY edges
            for pred in self.g.predecessors(node.id):
                edge_data = self.g.get_edge_data(pred, node.id)
                if edge_data and any(d.get("kind") == EdgeKind.CALLS.value for d in edge_data.values()):
                    if pred in self.nodes and pred not in visited:
                        callers.append(self.nodes[pred])
                        visited.add(pred)

        # Also check synthetic symbol target edges: symbol:<name>
        sym_id = f"symbol:{symbol}"
        if self.g.has_node(sym_id):
            for pred in self.g.predecessors(sym_id):
                edge_data = self.g.get_edge_data(pred, sym_id)
                if edge_data and any(d.get("kind") == EdgeKind.CALLS.value for d in edge_data.values()):
                    if pred in self.nodes and pred not in visited:
                        callers.append(self.nodes[pred])
                        visited.add(pred)

        return callers

    def callees(self, symbol: str) -> List[GraphNode]:
        """Finds functions/methods CALLED by the given symbol."""
        callees = []
        nodes = self.resolve_symbol(symbol)
        visited = set()
        
        for node in nodes:
            for succ in self.g.successors(node.id):
                edge_data = self.g.get_edge_data(node.id, succ)
                if edge_data and any(d.get("kind") == EdgeKind.CALLS.value for d in edge_data.values()):
                    if succ in self.nodes and succ not in visited:
                        callees.append(self.nodes[succ])
                        visited.add(succ)
                    elif succ.startswith("symbol:"):
                        # Resolve referenced symbol to concrete nodes if possible
                        sym_name = succ.replace("symbol:", "")
                        for concrete in self.resolve_symbol(sym_name):
                            if concrete.id not in visited:
                                callees.append(concrete)
                                visited.add(concrete.id)

        return callees

    def references(self, symbol: str) -> List[GraphNode]:
        """Finds symbols that reference or are referenced by this symbol."""
        refs = set()
        nodes = self.resolve_symbol(symbol)
        for node in nodes:
            # Check predecessors & successors
            for neighbor in list(self.g.predecessors(node.id)) + list(self.g.successors(node.id)):
                if neighbor in self.nodes:
                    refs.add(self.nodes[neighbor])
        return list(refs)

    def tests_for(self, symbol: str) -> List[GraphNode]:
        """Finds test functions that test, assert on, or exercise the given symbol."""
        test_nodes = []
        visited = set()
        nodes = self.resolve_symbol(symbol)
        target_ids = {n.id for n in nodes}
        target_ids.add(f"symbol:{symbol}")
        
        for tid in target_ids:
            if not self.g.has_node(tid):
                continue
            for pred in self.g.predecessors(tid):
                edge_data = self.g.get_edge_data(pred, tid)
                if edge_data and any(d.get("kind") in (EdgeKind.TESTS.value, EdgeKind.CALLS.value) for d in edge_data.values()):
                    if pred in self.nodes and pred not in visited:
                        node = self.nodes[pred]
                        if node.kind in (NodeKind.TEST_FUNCTION, NodeKind.TEST_CLASS) or "test" in node.file_path.lower():
                            test_nodes.append(node)
                            visited.add(pred)

        return test_nodes

    def inheritance(self, symbol: str) -> Dict[str, List[str]]:
        """Returns base classes and subclasses for a class symbol."""
        bases = []
        subclasses = []
        nodes = self.resolve_symbol(symbol)
        for node in nodes:
            if node.kind in (NodeKind.CLASS, NodeKind.TEST_CLASS):
                # Bases
                for succ in self.g.successors(node.id):
                    edge_data = self.g.get_edge_data(node.id, succ)
                    if edge_data and any(d.get("kind") == EdgeKind.INHERITS.value for d in edge_data.values()):
                        bases.append(succ.replace("symbol:", ""))
                # Subclasses
                for pred in self.g.predecessors(node.id):
                    edge_data = self.g.get_edge_data(pred, node.id)
                    if edge_data and any(d.get("kind") == EdgeKind.INHERITS.value for d in edge_data.values()):
                        if pred in self.nodes:
                            subclasses.append(self.nodes[pred].name)
        return {"bases": list(set(bases)), "subclasses": list(set(subclasses))}

    def imports(self, module_or_file: str) -> Dict[str, List[str]]:
        """Finds modules imported by, and modules that import, the given file/module."""
        file_path = module_or_file.replace("\\", "/")
        mod_id = f"module:{file_path}" if not module_or_file.startswith("module:") else module_or_file
        
        imported = []
        importers = []
        
        if self.g.has_node(mod_id):
            # Outgoing IMPORTS
            for succ in self.g.successors(mod_id):
                edge_data = self.g.get_edge_data(mod_id, succ)
                if edge_data and any(d.get("kind") == EdgeKind.IMPORTS.value for d in edge_data.values()):
                    imported.append(succ.replace("module:", "").replace("symbol:", ""))
            # Incoming IMPORTS
            for pred in self.g.predecessors(mod_id):
                edge_data = self.g.get_edge_data(pred, mod_id)
                if edge_data and any(d.get("kind") == EdgeKind.IMPORTS.value for d in edge_data.values()):
                    importers.append(pred.replace("module:", "").replace("symbol:", ""))

        return {"imported": list(set(imported)), "importers": list(set(importers))}

    def related_symbols(self, symbol: str, max_depth: int = 1) -> List[GraphNode]:
        """Finds related symbols in the immediate structural ego network (callers, callees, sibling methods)."""
        nodes = self.resolve_symbol(symbol)
        if not nodes:
            return []
            
        related = set()
        for node in nodes:
            # Sibling methods if class method
            parent_class = node.attributes.get("parent_class")
            if parent_class and parent_class in self.nodes:
                for child_id in self.g.successors(parent_class):
                    if child_id in self.nodes and child_id != node.id:
                        related.add(self.nodes[child_id])
                        
            # Callers & Callees
            for c in self.callers(node.name):
                related.add(c)
            for c in self.callees(node.name):
                related.add(c)
                
            # Direct references
            for r in self.references(node.name):
                if r.kind in (NodeKind.FUNCTION, NodeKind.METHOD, NodeKind.CLASS):
                    related.add(r)
                    
        return list(related)

    def get_symbol_summary(self, symbol: str) -> Dict[str, Any]:
        """Produces a structured inspection summary for an entity."""
        nodes = self.resolve_symbol(symbol)
        if not nodes:
            return {"symbol": symbol, "found": False}
            
        primary = nodes[0]
        callers = [c.name for c in self.callers(symbol)]
        callees = [c.name for c in self.callees(symbol)]
        tests = [t.name for t in self.tests_for(symbol)]
        inheritance = self.inheritance(symbol)
        
        return {
            "symbol": symbol,
            "found": True,
            "id": primary.id,
            "kind": primary.kind.value,
            "file_path": primary.file_path,
            "lines": [primary.start_line, primary.end_line],
            "signature": primary.signature,
            "docstring": primary.docstring,
            "callers": callers[:10],
            "callees": callees[:10],
            "tests": tests[:10],
            "inheritance": inheritance,
        }

    def save(self, file_path: str):
        """Serializes graph to disk JSON format."""
        os.makedirs(os.path.dirname(os.path.abspath(file_path)), exist_ok=True)
        data = {
            "repo_name": self.repo_name,
            "nodes": [node.to_dict() for node in self.nodes.values()],
            "edges": [],
        }
        for u, v, key, attrs in self.g.edges(keys=True, data=True):
            data["edges"].append({
                "source_id": u,
                "target_id": v,
                "kind": attrs.get("kind", key),
                "attributes": {k: val for k, val in attrs.items() if k != "kind"},
            })
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    @classmethod
    def load(cls, file_path: str) -> "RepositoryGraph":
        """Deserializes graph from disk JSON format."""
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        graph = cls(repo_name=data.get("repo_name", ""))
        for nd in data.get("nodes", []):
            graph.add_node(GraphNode.from_dict(nd))
        for ed in data.get("edges", []):
            graph.add_edge(GraphEdge.from_dict(ed))
        return graph
