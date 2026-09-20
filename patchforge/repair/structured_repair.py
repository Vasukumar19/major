"""Model-facing structured repair contract and robust output parser for PatchForge AI.

Ensures the model provides only code replacements and reasoning, while PatchForge
grounds the target AST node, source span, and location.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from patchforge.issue.problem import Problem
from patchforge.repair.schema import RepairAction, RepairUnit, StructuredRepairOutput
from patchforge.repository_intelligence.diagnosis import DiagnosisResult

logger = logging.getLogger(__name__)

SYSTEM_STRUCTURED_REPAIR_PROMPT = """You are PatchForge AI, an expert autonomous software-repair engine.
Your task is to fix the software defect by producing a surgical code replacement for the specified TARGET CODE UNIT.

CRITICAL RULES:
1. Do NOT generate unified diffs (no '---', '+++', or '@@' markers).
2. Do NOT generate git diffs or SEARCH/REPLACE conflict blocks.
3. You must output ONLY a valid JSON object matching the requested schema.
4. The 'replacement' field must contain ONLY valid, executable Python code to replace the target unit.
5. Adhere strictly to the behavioral invariant and diagnosis provided.
"""


class StructuredRepairPromptBuilder:
    """Builds compact, AST-grounded repair prompts without overwhelming the model's context."""

    @staticmethod
    def build_repair_prompt(
        unit: RepairUnit,
        problem: Optional[Problem] = None,
        diagnosis: Optional[DiagnosisResult] = None,
        callers: Optional[List[str]] = None,
        callees: Optional[List[str]] = None,
        state_flow_summary: str = "",
        surrounding_lines: int = 15,
        full_source: str = "",
    ) -> str:
        """Constructs a compact, structured repair prompt."""
        issue_sec = ""
        if problem:
            issue_stmt = getattr(problem, "problem_statement", "")[:1500]
            issue_sec = f"""### ISSUE SPECIFICATION:
{issue_stmt}
"""

        diag_sec = ""
        if diagnosis:
            diag_sec = f"""### BEHAVIORAL DIAGNOSIS:
Root Cause: {diagnosis.cause}
Required Invariant: {diagnosis.invariant}
Repair Strategy: {diagnosis.repair_strategy}
"""

        # Build surrounding context around the target unit
        source_context_sec = ""
        if full_source:
            lines = full_source.splitlines()
            start_ctx = max(1, unit.start_line - surrounding_lines)
            end_ctx = min(len(lines), unit.end_line + surrounding_lines)
            
            numbered_lines = []
            for i in range(start_ctx, end_ctx + 1):
                prefix = ">>> " if unit.start_line <= i <= unit.end_line else "    "
                numbered_lines.append(f"{prefix}{i:4d} | {lines[i-1]}")
            source_context_sec = "\n".join(numbered_lines)
        else:
            source_context_sec = unit.source_text

        # Bounded callers & callees (max 4 each)
        graph_evidence = []
        if callers:
            graph_evidence.append(f"Relevant Callers: {', '.join(callers[:4])}")
        if callees:
            graph_evidence.append(f"Relevant Callees: {', '.join(callees[:4])}")
        if state_flow_summary:
            graph_evidence.append(f"State Flow: {state_flow_summary[:300]}")
        
        graph_sec = ""
        if graph_evidence:
            graph_sec = "### REPOSITORY CONTEXT:\n" + "\n".join(graph_evidence) + "\n"

        prompt = f"""{issue_sec}
{diag_sec}
{graph_sec}
### REPAIR TARGET:
File: {unit.file_path}
Symbol: {unit.symbol} (Node: {unit.node_type}, Type: {unit.unit_type.value})
Target Lines: {unit.start_line} to {unit.end_line}

### SURROUNDING SOURCE CODE (>>> indicates target lines to be modified):
```python
{source_context_sec}
```

### TARGET CODE UNIT CURRENT SOURCE:
```python
{unit.source_text}
```

---
### REQUIRED OUTPUT FORMAT:
Respond with ONLY a single valid JSON object in the following schema:
```json
{{
  "repair_action": "{unit.unit_type.value.lower() if unit.unit_type.value.startswith('replace') else 'replace_' + unit.unit_type.value.lower()}",
  "target_unit_id": "{unit.id}",
  "target_symbol": "{unit.symbol}",
  "replacement": "<exact replacement Python code>",
  "reasoning": "<brief justification>",
  "invariant": "<invariant preserved>"
}}
```
"""
        return prompt


class StructuredRepairParser:
    """Robust parser for model-generated structured repair outputs."""

    VALID_ACTIONS = {
        "replace_expression": RepairAction.REPLACE_EXPRESSION,
        "replace_statement": RepairAction.REPLACE_STATEMENT,
        "replace_block": RepairAction.REPLACE_BLOCK,
        "replace_statement_block": RepairAction.REPLACE_BLOCK,
        "replace_function": RepairAction.REPLACE_METHOD,
        "replace_function_body": RepairAction.REPLACE_FUNCTION_BODY,
        "replace_method": RepairAction.REPLACE_METHOD,
        "insert_statement": RepairAction.INSERT_STATEMENT,
        "delete_statement": RepairAction.DELETE_STATEMENT,
        "replace_decorator": RepairAction.REPLACE_DECORATOR,
        "modify_import": RepairAction.MODIFY_IMPORT,
        "multi_site": RepairAction.MULTI_SITE,
    }

    @staticmethod
    def parse(response_text: str, target_unit: RepairUnit) -> StructuredRepairOutput:
        """Parses model response into a validated StructuredRepairOutput."""
        # 1. Strip <think> tags from reasoning models
        cleaned_text = response_text
        if "<think>" in cleaned_text and "</think>" in cleaned_text:
            cleaned_text = cleaned_text.split("</think>")[-1].strip()
        elif "<think>" in cleaned_text:
            cleaned_text = cleaned_text.split("<think>")[-1].strip()

        # 2. Reject raw diff formats if model output is clearly a diff
        if cleaned_text.startswith("diff --git") or "--- a/" in cleaned_text or "<<<<<<< SEARCH" in cleaned_text:
            # Check if there is an embedded JSON inside
            pass

        # 3. Attempt JSON extraction
        data = None

        # Strategy A: Extract from ```json ... ``` block
        json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned_text, re.DOTALL)
        if json_match:
            try:
                data = json.loads(json_match.group(1))
            except Exception:
                pass

        # Strategy B: Find outermost JSON object { ... }
        if not data:
            brace_start = cleaned_text.find("{")
            brace_end = cleaned_text.rfind("}")
            if brace_start != -1 and brace_end > brace_start:
                json_candidate = cleaned_text[brace_start : brace_end + 1]
                try:
                    data = json.loads(json_candidate)
                except Exception:
                    # Clean escaped newlines or trailing commas
                    cleaned_json = re.sub(r",\s*([\]}])", r"\1", json_candidate)
                    try:
                        data = json.loads(cleaned_json)
                    except Exception:
                        pass

        # Strategy C: If JSON extraction succeeded
        if isinstance(data, dict):
            raw_action = str(data.get("repair_action", "")).lower()
            action = StructuredRepairParser.VALID_ACTIONS.get(raw_action)
            if not action:
                # Default to unit type
                action_name = f"replace_{target_unit.unit_type.value.lower()}"
                action = StructuredRepairParser.VALID_ACTIONS.get(action_name, RepairAction.REPLACE_STATEMENT)

            replacement = data.get("replacement", "")
            if not replacement:
                # Check for legacy SEARCH/REPLACE inside action/arguments or data
                patch_cand = data.get("action", {}).get("arguments", {}).get("patch_text", "") or data.get("patch_text", "")
                sr_match = re.search(r"<<<<<<< SEARCH\s*\n.*?\n=======\s*\n(.*?)\n>>>>>>> REPLACE", patch_cand, re.DOTALL)
                if sr_match:
                    replacement = sr_match.group(1).strip("\r\n")

            # Clean up replacement if wrapped in code fence
            replacement = StructuredRepairParser._clean_code(replacement)

            return StructuredRepairOutput(
                repair_action=action.value,
                target_unit_id=data.get("target_unit_id", target_unit.id),
                target_symbol=data.get("target_symbol", target_unit.symbol),
                replacement=replacement,
                reasoning=data.get("reasoning", ""),
                invariant=data.get("invariant", ""),
                confidence=float(data.get("confidence", 1.0)),
                sub_edits=data.get("sub_edits", []),
            )

        # Strategy D: Recovery fallback for Python code fence when JSON fails
        code_fence_match = re.search(r"```(?:python)?\s*\n(.*?)\n```", cleaned_text, re.DOTALL)
        if code_fence_match:
            code_candidate = code_fence_match.group(1).strip()
            # Verify candidate doesn't contain diff headers
            if not code_candidate.startswith("---") and not code_candidate.startswith("@@"):
                action_name = f"replace_{target_unit.unit_type.value.lower()}"
                action = StructuredRepairParser.VALID_ACTIONS.get(action_name, RepairAction.REPLACE_STATEMENT)
                return StructuredRepairOutput(
                    repair_action=action.value,
                    target_unit_id=target_unit.id,
                    target_symbol=target_unit.symbol,
                    replacement=code_candidate,
                    reasoning="Extracted from code block fallback",
                    confidence=0.8,
                )

        raise ValueError(
            f"Failed to extract structured repair JSON from model response. Response snippet: {cleaned_text[:300]}"
        )

    @staticmethod
    def _clean_code(code: str) -> str:
        """Strips surrounding backticks and cleans code string."""
        if not code:
            return ""
        code = code.strip()
        if code.startswith("```"):
            lines = code.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            code = "\n".join(lines)
        return code
