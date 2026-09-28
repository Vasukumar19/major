"""Model-facing structured repair contract, robust parser, and two-layer validation for PatchForge AI.

Ensures the model provides reliable code replacements bound strictly to PatchForge-verified targets,
with robust recovery from unescaped quotes/docstrings and multi-tier fallbacks.
"""
from __future__ import annotations

import ast
import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from patchforge.issue.problem import Problem
from patchforge.repair.schema import (
    RepairAction,
    RepairUnit,
    RepairUnitType,
    SchemaValidationResult,
    SemanticValidationResult,
    StructuredRepairOutput,
    UNIT_CANONICAL_ACTIONS,
    UNIT_PERMITTED_ACTIONS,
)
from patchforge.repository_intelligence.diagnosis import DiagnosisResult

logger = logging.getLogger(__name__)

SYSTEM_STRUCTURED_REPAIR_PROMPT = """You are PatchForge AI, an expert autonomous software-repair engine.
Your task is to fix the software defect by producing a surgical code replacement for the specified TARGET CODE UNIT.

CRITICAL RULES:
1. Do NOT generate unified diffs (no '---', '+++', or '@@' markers).
2. Do NOT generate git diffs or SEARCH/REPLACE conflict blocks.
3. You must output ONLY a valid JSON object matching the requested schema.
4. The 'replacement' field must contain ONLY valid, executable Python code to replace the target unit.
5. If your replacement code contains double quotes (e.g. docstrings \"\"\" or strings \"...\"), escape them properly as \\\" or use single quotes ' so the JSON is strictly valid.
6. Adhere strictly to the behavioral invariant and diagnosis provided.
"""


class StructuredRepairPromptBuilder:
    """Builds compact, AST-grounded repair prompts across primary and fallback tiers."""

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
        contract_spec: str = "",
        requirement_matrix_spec: str = "",
        error_feedback: str = "",
    ) -> str:
        """Constructs a compact, structured Tier 1 repair prompt."""
        feedback_sec = ""
        if error_feedback:
            feedback_sec = f"""### PREVIOUS REPAIR ATTEMPT FEEDBACK (CRITICAL - YOU MUST FIX THIS):
{error_feedback}

"""

        issue_sec = ""
        if problem:
            issue_stmt = getattr(problem, "problem_statement", "")[:1500]
            issue_sec = f"""### ISSUE SPECIFICATION:
{issue_stmt}
"""

        contract_sec = f"{contract_spec}\n" if contract_spec else ""
        req_sec = f"{requirement_matrix_spec}\n" if requirement_matrix_spec else ""

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

        canonical_action = UNIT_CANONICAL_ACTIONS.get(unit.unit_type, RepairAction.REPLACE_STATEMENT).value

        prompt = f"""{feedback_sec}{issue_sec}
{contract_sec}{req_sec}{diag_sec}
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

### MANDATORY IMPLEMENTATION RULES:
1. If CONTRACT SPECIFICATION specifies required parameter(s), you MUST update the definition signature to include them with default values (e.g. `def {unit.symbol}(..., <param>=<default>):`).
2. If REJECTED/FORBIDDEN PARAMETERS are specified, do NOT introduce them under any circumstances.
3. Adhere strictly to the BEHAVIORAL DIAGNOSIS repair strategy and invariants.
4. Maintain exact indentation matching the target code unit.

---
### REQUIRED OUTPUT FORMAT:
Respond with ONLY a single valid JSON object in the following schema:
```json
{{
  "repair_action": "{canonical_action}",
  "target_unit_id": "{unit.id}",
  "target_symbol": "{unit.symbol}",
  "replacement": "<exact replacement Python code>",
  "reasoning": "<brief justification>",
  "invariant": "<invariant preserved>"
}}
```
Note: Ensure inner quotes in the replacement string are escaped as \\\" or use single quotes.
"""
        return prompt

    @staticmethod
    def build_minimal_prompt(unit: RepairUnit, error_feedback: str = "") -> str:
        """Constructs a Tier 2 minimal target-bound prompt."""
        feedback_sec = f"\n[PREVIOUS ATTEMPT ERROR]: {error_feedback}\n" if error_feedback else ""
        canonical_action = UNIT_CANONICAL_ACTIONS.get(unit.unit_type, RepairAction.REPLACE_STATEMENT).value

        return f"""{feedback_sec}
You are repairing a specific verified code target.
Target ID: {unit.id}
Target Symbol: {unit.symbol} ({unit.unit_type.value})
Current Target Code:
```python
{unit.source_text}
```

Provide ONLY the minimal JSON replacement matching this schema:
```json
{{
  "repair_action": "{canonical_action}",
  "target_unit_id": "{unit.id}",
  "replacement": "<exact replacement Python code>"
}}
```
Do NOT output commentary, diff markers, or explanations.
"""

    @staticmethod
    def build_replacement_only_prompt(unit: RepairUnit, error_feedback: str = "") -> str:
        """Constructs a Tier 3 replacement-only prompt."""
        feedback_sec = f"\n[PREVIOUS ATTEMPT ERROR]: {error_feedback}\n" if error_feedback else ""

        return f"""{feedback_sec}
Return ONLY the replacement Python code for the target code unit below.
Target ID: {unit.id}
Type: {unit.unit_type.value}
Current Code:
```python
{unit.source_text}
```

Format: Return ONLY the raw Python replacement code inside a ```python ``` code block.
Do NOT output JSON, unified diffs, or markdown commentary.
"""


class StructuredRepairParser:
    """Robust parser with Layer A (Schema) and Layer B (Semantic AST) validation."""

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
        "replace_import_block": RepairAction.MODIFY_IMPORT,
        "replace_class_member": RepairAction.REPLACE_STATEMENT,
        "multi_site": RepairAction.MULTI_SITE,
    }

    DISALLOWED_MARKERS = [
        "diff --git",
        "<<<<<<< SEARCH",
        "=======",
        ">>>>>>> REPLACE",
        "@@ -",
    ]

    @staticmethod
    def parse(response_text: str, target_unit: RepairUnit) -> StructuredRepairOutput:
        """Parses model response into a validated StructuredRepairOutput with Layer A & B checks."""
        # 1. Strip reasoning <think> tags
        cleaned_text = response_text
        if "<think>" in cleaned_text:
            if "</think>" in cleaned_text:
                cleaned_text = cleaned_text.split("</think>")[-1].strip()
            else:
                cleaned_text = cleaned_text.split("<think>")[-1].strip()

        # 2. Check for Ambiguous Multiple JSON objects
        json_blocks = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned_text, re.DOTALL)
        if len(json_blocks) > 1:
            valid_reps = [b for b in json_blocks if '"replacement"' in b]
            if len(valid_reps) > 1:
                schema_res = SchemaValidationResult(
                    valid=False,
                    error="Multiple ambiguous repair JSON blocks found in model response.",
                    failure_category="AMBIGUOUS_REPAIR_OUTPUT",
                    raw_snippet=cleaned_text[:300],
                )
                raise ValueError(f"AMBIGUOUS_REPAIR_OUTPUT: Multiple repair blocks detected ({len(valid_reps)}).")

        # 3. Attempt JSON Extraction & Repair
        data = None
        is_repaired = False

        # Attempt A: Standard parse from ```json ... ``` block
        if json_blocks:
            try:
                data = json.loads(json_blocks[0])
            except Exception:
                pass

        # Attempt B: Direct parse
        if not data:
            start_b = cleaned_text.find("{")
            end_b = cleaned_text.rfind("}")
            if start_b != -1 and end_b > start_b:
                candidate = cleaned_text[start_b : end_b + 1]
                try:
                    data = json.loads(candidate)
                except Exception:
                    pass

        # Attempt C: Boundary-aware quote and docstring repair
        if not data:
            repaired_data = StructuredRepairParser._repair_and_extract_json(cleaned_text)
            if repaired_data:
                data = repaired_data
                is_repaired = True

        # Attempt D: Fallback Python code fence if no valid JSON
        if not data:
            code_fence_match = re.search(r"```(?:python)?\s*\n(.*?)\n```", cleaned_text, re.DOTALL)
            if code_fence_match:
                code_cand = code_fence_match.group(1).strip()
                if not any(m in code_cand for m in StructuredRepairParser.DISALLOWED_MARKERS):
                    canonical_action = UNIT_CANONICAL_ACTIONS.get(target_unit.unit_type, RepairAction.REPLACE_STATEMENT).value
                    out = StructuredRepairOutput(
                        repair_action=canonical_action,
                        target_unit_id=target_unit.id,
                        target_symbol=target_unit.symbol,
                        replacement=StructuredRepairParser._clean_code(code_cand),
                        reasoning="Extracted from raw Python code block fallback",
                        confidence=0.85,
                        schema_validation=SchemaValidationResult(valid=True, repaired=True, raw_snippet=code_cand[:100]),
                    )
                    sem_res = StructuredRepairParser.validate_semantic_repair(out, target_unit)
                    out.semantic_validation = sem_res
                    if not sem_res.valid:
                        raise ValueError(f"SEMANTIC_VALIDATION_FAILURE: {'; '.join(sem_res.errors)}")
                    return out

        if not isinstance(data, dict):
            raise ValueError(
                f"INVALID_JSON: Failed to extract structured repair JSON. Response snippet: {cleaned_text[:300]}"
            )

        # --- LAYER A: SCHEMA VALIDATION ---
        # 1. Target Unit ID validation & auto-binding
        raw_target_id = str(data.get("target_unit_id", "")).strip()
        target_unit_id = target_unit.id

        # 2. Repair Action validation & auto-checking
        raw_action = str(data.get("repair_action", "")).lower().strip()
        action = StructuredRepairParser.VALID_ACTIONS.get(raw_action)
        permitted = UNIT_PERMITTED_ACTIONS.get(target_unit.unit_type, {RepairAction.REPLACE_STATEMENT})

        if action and action in permitted:
            final_action = action.value
        else:
            canonical = UNIT_CANONICAL_ACTIONS.get(target_unit.unit_type, RepairAction.REPLACE_STATEMENT)
            final_action = canonical.value

        # 3. Replacement validation
        replacement = data.get("replacement")
        if replacement is None:
            raise ValueError("MISSING_REQUIRED_FIELD: 'replacement' field is missing from JSON.")

        if not isinstance(replacement, str):
            replacement = str(replacement)

        replacement = StructuredRepairParser._clean_code(replacement)

        if not replacement and final_action != RepairAction.DELETE_STATEMENT.value:
            raise ValueError("EMPTY_REPLACEMENT: Replacement code is empty.")

        schema_res = SchemaValidationResult(
            valid=True,
            repaired=is_repaired,
            raw_snippet=str(data)[:200],
        )

        output = StructuredRepairOutput(
            repair_action=final_action,
            target_unit_id=target_unit_id,
            target_symbol=data.get("target_symbol", target_unit.symbol),
            replacement=replacement,
            reasoning=str(data.get("reasoning", "")),
            invariant=str(data.get("invariant", "")),
            confidence=float(data.get("confidence", 1.0)),
            sub_edits=data.get("sub_edits", []),
            schema_validation=schema_res,
        )

        # --- LAYER B: SEMANTIC REPAIR VALIDATION ---
        sem_res = StructuredRepairParser.validate_semantic_repair(output, target_unit)
        output.semantic_validation = sem_res
        if not sem_res.valid:
            raise ValueError(f"SEMANTIC_VALIDATION_FAILURE: {'; '.join(sem_res.errors)}")

        return output

    @staticmethod
    def validate_semantic_repair(output: StructuredRepairOutput, target_unit: RepairUnit) -> SemanticValidationResult:
        """Layer B: Validates AST category, syntax, and safety invariants."""
        res = SemanticValidationResult(valid=True)
        errors = []

        rep = output.replacement.strip()
        if not rep and output.repair_action == RepairAction.DELETE_STATEMENT.value:
            return res

        # 1. Check for diff marker leakage
        for marker in StructuredRepairParser.DISALLOWED_MARKERS:
            if marker in rep:
                res.no_target_escape = False
                res.valid = False
                errors.append(f"Diff marker '{marker}' leaked into replacement code.")

        # 2. Check AST Category
        unit_type = target_unit.unit_type
        if unit_type == RepairUnitType.EXPRESSION:
            try:
                ast.parse(rep, mode="eval")
                res.ast_category_ok = True
            except SyntaxError as e:
                try:
                    ast.parse(rep, mode="exec")
                    res.ast_category_ok = True
                except SyntaxError:
                    res.ast_category_ok = False
                    res.valid = False
                    errors.append(f"Expression syntax error: {e.msg}")

        elif unit_type in (RepairUnitType.STATEMENT, RepairUnitType.STATEMENT_BLOCK, RepairUnitType.CLASS_MEMBER):
            try:
                ast.parse(rep, mode="exec")
                res.ast_category_ok = True
            except SyntaxError as e:
                res.ast_category_ok = False
                res.valid = False
                errors.append(f"Statement syntax error: {e.msg}")

        elif unit_type in (RepairUnitType.FUNCTION, RepairUnitType.METHOD):
            if output.repair_action == RepairAction.REPLACE_FUNCTION_BODY.value:
                try:
                    ast.parse(rep, mode="exec")
                    res.ast_category_ok = True
                except SyntaxError as e:
                    res.ast_category_ok = False
                    res.valid = False
                    errors.append(f"Function body syntax error: {e.msg}")
            else:
                try:
                    tree = ast.parse(rep, mode="exec")
                    has_func = any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) for n in tree.body)
                    res.ast_category_ok = True
                except SyntaxError as e:
                    res.ast_category_ok = False
                    res.valid = False
                    errors.append(f"Function/Method syntax error: {e.msg}")

        elif unit_type == RepairUnitType.DECORATOR:
            try:
                ast.parse(f"{rep}\ndef __dummy(): pass", mode="exec")
                res.ast_category_ok = True
            except SyntaxError as e:
                res.ast_category_ok = False
                res.valid = False
                errors.append(f"Decorator syntax error: {e.msg}")

        elif unit_type == RepairUnitType.IMPORT_BLOCK:
            try:
                ast.parse(rep, mode="exec")
                res.ast_category_ok = True
            except SyntaxError as e:
                res.ast_category_ok = False
                res.valid = False
                errors.append(f"Import syntax error: {e.msg}")

        res.errors = errors
        res.valid = len(errors) == 0
        return res

    @staticmethod
    def _repair_and_extract_json(text: str) -> Optional[Dict[str, Any]]:
        """Repairs JSON with unescaped docstrings or quotes in the replacement field."""
        start_b = text.find("{")
        end_b = text.rfind("}")
        if start_b == -1 or end_b <= start_b:
            return None

        candidate = text[start_b : end_b + 1]

        pattern = re.compile(
            r'("replacement"\s*:\s*")(.*?)("\s*,\s*"(?:reasoning|invariant|confidence|sub_edits)"|"\s*\})',
            re.DOTALL,
        )
        match = pattern.search(candidate)
        if match:
            prefix = candidate[: match.start(2)]
            rep_content = match.group(2)
            suffix = candidate[match.end(2) :]

            escaped_rep = []
            i = 0
            while i < len(rep_content):
                c = rep_content[i]
                if c == '"':
                    num_slashes = 0
                    j = i - 1
                    while j >= 0 and rep_content[j] == "\\":
                        num_slashes += 1
                        j -= 1
                    if num_slashes % 2 == 0:
                        escaped_rep.append('\\"')
                    else:
                        escaped_rep.append('"')
                elif c == "\n":
                    escaped_rep.append("\\n")
                elif c == "\r":
                    escaped_rep.append("\\r")
                elif c == "\t":
                    escaped_rep.append("\\t")
                else:
                    escaped_rep.append(c)
                i += 1

            repaired_candidate = prefix + "".join(escaped_rep) + suffix
            repaired_candidate = re.sub(r",\s*([\]}])", r"\1", repaired_candidate)
            try:
                return json.loads(repaired_candidate)
            except Exception:
                pass

        # Regex key-value extraction fallback
        result = {}
        action_m = re.search(r'"repair_action"\s*:\s*"([^"]+)"', text)
        if action_m:
            result["repair_action"] = action_m.group(1)

        unit_m = re.search(r'"target_unit_id"\s*:\s*"([^"]+)"', text)
        if unit_m:
            result["target_unit_id"] = unit_m.group(1)

        sym_m = re.search(r'"target_symbol"\s*:\s*"([^"]+)"', text)
        if sym_m:
            result["target_symbol"] = sym_m.group(1)

        rep_m = re.search(
            r'"replacement"\s*:\s*"(.*?)(?:",\s*"(?:reasoning|invariant|confidence|sub_edits)"|"\s*\})',
            text,
            re.DOTALL,
        )
        if rep_m:
            raw_rep = rep_m.group(1)
            try:
                cleaned = raw_rep.encode("utf-8").decode("unicode_escape", errors="replace")
            except Exception:
                cleaned = raw_rep.replace('\\"', '"').replace("\\n", "\n")
            result["replacement"] = cleaned
            return result

        return None

    @staticmethod
    def _clean_code(code: str) -> str:
        """Strips surrounding backticks and cleans code string."""
        if not code:
            return ""
        code = code.strip("\r\n").rstrip()
        if code.lstrip().startswith("```"):
            lines = code.splitlines()
            if lines[0].lstrip().startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            code = "\n".join(lines)
            code = code.strip("\r\n").rstrip()
        # Normalize smart/curly unicode quotation marks that invalidate Python syntax
        code = code.replace("\u201c", '"').replace("\u201d", '"').replace("\u2018", "'").replace("\u2019", "'")
        return code
