"""Reasoning, specification, and planning tools for PatchForge v0.3."""
from __future__ import annotations

from typing import Any

from patchforge.reasoning.hypothesis import Hypothesis
from patchforge.reasoning.plan import RepairPlan
from patchforge.reasoning.specification import RepairSpecification
from patchforge.tools.base import Tool, ToolResult


class FormulateHypothesisTool(Tool):
    name = "formulate_hypothesis"
    description = (
        "Formulate a concrete causal hypothesis explaining the root cause and proposed repair approach."
    )
    parameters = {
        "type": "object",
        "properties": {
            "root_cause": {"type": "string", "description": "Explanation of the root cause mechanism"},
            "repair_approach": {"type": "string", "description": "High-level fix strategy"},
            "affected_file": {"type": "string", "description": "Primary file requiring modification"},
            "expected_behavior": {"type": "string", "description": "Expected behavioral outcome after fix"},
        },
        "required": ["root_cause", "repair_approach"],
    }

    def execute(self, args: dict[str, Any], state: Any = None) -> ToolResult:
        root_cause = args.get("root_cause", "")
        repair_approach = args.get("repair_approach", "")
        affected_file = args.get("affected_file", "")
        expected_behavior = args.get("expected_behavior", "")

        hyp_id = f"H{len(state.hypotheses) + 1}" if state and hasattr(state, "hypotheses") else "H1"
        hyp = Hypothesis(
            id=hyp_id,
            description=f"{root_cause}. Proposed fix: {repair_approach}",
            affected_files=[affected_file] if affected_file else [],
            expected_behavior=expected_behavior,
            confidence=0.85,
        )

        if state and hasattr(state, "hypotheses"):
            state.hypotheses.append(hyp)
            state.active_hypothesis = hyp

        return ToolResult(
            tool_name=self.name,
            status="SUCCESS",
            data={"hypothesis": hyp.to_dict()},
            message=f"Formulated hypothesis {hyp.id}: {hyp.description}",
        )


class ProposePlanTool(Tool):
    name = "propose_plan"
    description = (
        "Propose a concrete repair plan specifying target file, region, intended change, and validation tests."
    )
    parameters = {
        "type": "object",
        "properties": {
            "target_file": {"type": "string", "description": "Relative path to file to edit"},
            "intended_change": {"type": "string", "description": "Exact modification to perform"},
            "target_symbols": {"type": "array", "items": {"type": "string"}, "description": "Symbols modified"},
            "rationale": {"type": "string", "description": "Why this change satisfies the specification"},
        },
        "required": ["target_file", "intended_change"],
    }

    def execute(self, args: dict[str, Any], state: Any = None) -> ToolResult:
        target_file = args.get("target_file", "")
        intended_change = args.get("intended_change", "")
        target_symbols = args.get("target_symbols", [])
        rationale = args.get("rationale", "")

        plan = RepairPlan(
            target_file=target_file,
            target_symbols=target_symbols,
            intended_change=intended_change,
            rationale=rationale,
        )

        if state and hasattr(state, "active_plan"):
            state.active_plan = plan

        return ToolResult(
            tool_name=self.name,
            status="SUCCESS",
            data={"plan": plan.to_dict()},
            message=f"Proposed repair plan for {target_file}: {intended_change}",
        )


class SpecifyRepairTool(Tool):
    name = "specify_repair"
    description = "Define or update the explicit repair specification and constraints."
    parameters = {
        "type": "object",
        "properties": {
            "expected_behavior": {"type": "string", "description": "Desired behavior"},
            "observed_behavior": {"type": "string", "description": "Buggy behavior observed"},
            "constraints": {"type": "array", "items": {"type": "string"}, "description": "Key invariants"},
            "must_preserve": {"type": "array", "items": {"type": "string"}, "description": "Behaviors to preserve"},
        },
    }

    def execute(self, args: dict[str, Any], state: Any = None) -> ToolResult:
        exp = args.get("expected_behavior", "")
        obs = args.get("observed_behavior", "")
        constraints = args.get("constraints", [])
        must_preserve = args.get("must_preserve", [])

        spec = RepairSpecification(
            expected_behavior=exp,
            observed_behavior=obs,
            constraints=constraints,
            must_preserve=must_preserve,
        )

        if state and hasattr(state, "specification"):
            state.specification = spec

        return ToolResult(
            tool_name=self.name,
            status="SUCCESS",
            data={"specification": spec.to_dict()},
            message="Repair specification updated successfully.",
        )
