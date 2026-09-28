"""PatchForge Contract Extraction Engine: derives explicit API contracts and behavioral invariants."""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


from enum import Enum


class ContractCategory(str, Enum):
    API_SIGNATURE = "API_SIGNATURE"
    ALGORITHMIC_BEHAVIORAL = "ALGORITHMIC_BEHAVIORAL"
    MIXED = "MIXED"


@dataclass
class APIParameter:
    """Represents a function or method parameter requirement."""
    name: str
    type_annotation: str = ""
    default_value: Optional[str] = None
    is_kwarg_only: bool = False
    origin: str = "ISSUE_EXPLICIT"  # TEST_OBSERVED, ISSUE_EXPLICIT, HINTS_TEXT, EXISTING_AST

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "type_annotation": self.type_annotation,
            "default_value": self.default_value,
            "is_kwarg_only": self.is_kwarg_only,
            "origin": self.origin,
        }

    def format_signature_part(self) -> str:
        res = self.name
        if self.type_annotation:
            res += f": {self.type_annotation}"
        if self.default_value is not None:
            res += f" = {self.default_value}"
        return res


@dataclass
class APIContract:
    """Explicit behavioral and interface contract for a target repair symbol."""
    target_symbol: str
    category: str = ContractCategory.API_SIGNATURE.value
    confidence: float = 1.0
    parameters: List[APIParameter] = field(default_factory=list)
    forbidden_parameters: List[str] = field(default_factory=list)
    return_type: str = ""
    behavioral_invariants: List[str] = field(default_factory=list)
    expected_exceptions: List[str] = field(default_factory=list)
    handled_exceptions: List[str] = field(default_factory=list)
    forbidden_mutations: List[str] = field(default_factory=list)
    raw_call_examples: List[str] = field(default_factory=list)

    @property
    def is_signature_contract(self) -> bool:
        return self.category in (ContractCategory.API_SIGNATURE.value, ContractCategory.MIXED.value) and self.confidence >= 0.7

    def get_parameter(self, name: str) -> Optional[APIParameter]:
        for p in self.parameters:
            if p.name == name:
                return p
        return None

    def required_param_names(self) -> List[str]:
        return [p.name for p in self.parameters]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target_symbol": self.target_symbol,
            "category": self.category,
            "confidence": self.confidence,
            "is_signature_contract": self.is_signature_contract,
            "parameters": [p.to_dict() for p in self.parameters],
            "forbidden_parameters": self.forbidden_parameters,
            "return_type": self.return_type,
            "behavioral_invariants": self.behavioral_invariants,
            "expected_exceptions": self.expected_exceptions,
            "handled_exceptions": self.handled_exceptions,
            "forbidden_mutations": self.forbidden_mutations,
            "raw_call_examples": self.raw_call_examples,
        }

    def format_for_prompt(self) -> str:
        lines = [f"=== CONTRACT SPECIFICATION: `{self.target_symbol}` ==="]
        if self.parameters:
            params_str = ", ".join(p.format_signature_part() for p in self.parameters)
            lines.append(f"Required/Extended Signature: `{self.target_symbol}(..., {params_str})`")
            for p in self.parameters:
                origin_note = f" (detected via {p.origin})" if p.origin else ""
                lines.append(f"  * Parameter `{p.name}`: type={p.type_annotation or 'Any'}, default={p.default_value}{origin_note}")

        if self.forbidden_parameters:
            lines.append(f"REJECTED/FORBIDDEN PARAMETERS (DO NOT INTRODUCE): {', '.join(self.forbidden_parameters)}")

        if self.behavioral_invariants:
            lines.append("Behavioral Invariants (MUST PRESERVE):")
            for inv in self.behavioral_invariants:
                lines.append(f"  * {inv}")

        if self.expected_exceptions:
            lines.append(f"Required Exceptions to Raise/Propagate: {', '.join(self.expected_exceptions)}")

        if self.handled_exceptions:
            lines.append(f"Exceptions to Catch & Handle/Wrap: {', '.join(self.handled_exceptions)}")

        if self.raw_call_examples:
            lines.append("Observed API Usage / Test Invocations:")
            for ex in self.raw_call_examples[:3]:
                lines.append(f"  * `{ex}`")

        return "\n".join(lines)


class ContractApplicabilityClassifier:
    """Classifies whether an issue/defect requires an API signature modification or algorithmic repair."""

    SIGNATURE_PATTERNS = [
        re.compile(r"\b(?:add|new|accept|support|introduce)\s+(?:an?\s+)?(?:parameter|argument|kwarg|keyword\s+argument|option|flag)\b", re.IGNORECASE),
        re.compile(r"\b(?:signature|signature\s+of|parameter\s+list)\b", re.IGNORECASE),
        re.compile(r"\b(?:takes?\s+no\s+arguments?|unexpected\s+keyword\s+argument)\b", re.IGNORECASE),
        re.compile(r"\b(?:rather\s+than\s+mode\s+strings|mode\s+parameter|text\s+parameter)\b", re.IGNORECASE),
    ]

    @classmethod
    def classify(cls, problem_statement: str, hints_text: str = "") -> Tuple[ContractCategory, float]:
        text = f"{problem_statement}\n{hints_text}"
        sig_matches = sum(1 for pat in cls.SIGNATURE_PATTERNS if pat.search(text))
        hints_has_kwarg = bool(re.search(r"`[a-zA-Z_]\w*=[^`]+`", hints_text or ""))

        if sig_matches >= 2 or (sig_matches >= 1 and hints_has_kwarg):
            return ContractCategory.API_SIGNATURE, 0.95
        elif sig_matches == 1 or hints_has_kwarg:
            return ContractCategory.MIXED, 0.75
        else:
            return ContractCategory.ALGORITHMIC_BEHAVIORAL, 0.2


class ContractExtractor:
    """Extracts explicit API parameters, invariants, and exception contracts from text and AST."""

    NON_PARAM_TOKENS = {
        "python", "to", "code1", "code", "args", "file", "self", "line",
        "host", "port", "timeout", "data", "amt", "version", "path",
        "true", "false", "none", "kwargs", "var", "test", "tests", "name",
        "value", "key", "result", "expected", "actual", "obj", "item",
    }

    # Patterns for keyword arguments in function calls: e.g. func(..., text=False, mode='r')
    KWARG_CALL_PATTERN = re.compile(
        r"(?:([A-Za-z_][A-Za-z0-9_\.]*)\s*\([^)]*?\b([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*([^,\)\s]+)[^)]*?\))"
    )
    # Generic kwarg assignment in hints/code: e.g. `text=False` or `text=True`
    KWARG_ASSIGN_PATTERN = re.compile(
        r"`?\b([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*(True|False|None|'[^']*'|\"[^\"]*\"|[0-9]+)\b`?"
    )
    # Parameter hint pattern: e.g. "a text=True parameter", "parameter text", "text argument"
    PARAM_HINT_PATTERN = re.compile(
        r"\b(?:parameter|argument|option|flag)\s+`?([a-zA-Z_][a-zA-Z0-9_]*)`?|\b`?([a-zA-Z_][a-zA-Z0-9_]*)`?\s+(?:parameter|argument|keyword argument|kwarg)\b",
        re.IGNORECASE,
    )
    # Exception handling pattern: e.g. "catch socket.error", "wrap in ConnectionError"
    CATCH_EXCEPTION_PATTERN = re.compile(
        r"(?:catch|handle|intercept|trap)\s+([A-Za-z_][A-Za-z0-9_\.]*(?:Error|Exception))",
        re.IGNORECASE,
    )
    # Pattern for explicitly rejected alternative parameters: e.g. "rather than mode strings", "instead of mode"
    REJECT_PARAM_PATTERN = re.compile(
        r"\b(?:rather than|instead of|don't use|do not use|avoid|replace)\s+`?([a-zA-Z_][a-zA-Z0-9_]*)\b",
        re.IGNORECASE,
    )
    RAISE_EXCEPTION_PATTERN = re.compile(
        r"(?:raise|throw|wrap in|convert to)\s+([A-Za-z_][A-Za-z0-9_\.]*(?:Error|Exception))",
        re.IGNORECASE,
    )

    @classmethod
    def extract_contract(
        cls,
        problem_statement: str,
        hints_text: str = "",
        target_symbol: str = "",
        existing_source: str = "",
    ) -> APIContract:
        """Derives the API contract for a target symbol."""
        combined_text = f"{problem_statement}\n{hints_text}"
        category, confidence = ContractApplicabilityClassifier.classify(problem_statement, hints_text)

        contract = APIContract(
            target_symbol=target_symbol or "target",
            category=category.value,
            confidence=confidence,
        )

        import keyword

        def is_valid_param_name(name: str) -> bool:
            if not name or not name.isidentifier():
                return False
            if keyword.iskeyword(name):
                return False
            if name.lower() in ("self", "cls", "true", "false", "none", "the", "this", "that", "an", "a", "return", "def", "class"):
                return False
            if name.lower() in cls.NON_PARAM_TOKENS:
                return False
            return True

        # 0. Scan for explicitly rejected parameters
        for m in cls.REJECT_PARAM_PATTERN.finditer(combined_text):
            rej = m.group(1).lower()
            if is_valid_param_name(rej) and rej not in contract.forbidden_parameters:
                contract.forbidden_parameters.append(rej)

        # 1. Inspect existing AST if available
        if existing_source and target_symbol:
            cls._enrich_from_existing_ast(contract, existing_source, target_symbol)

        # 2. Extract observed call invocations and keyword arguments ONLY if applicable
        observed_params: Dict[str, APIParameter] = {}

        if category in (ContractCategory.API_SIGNATURE, ContractCategory.MIXED):
            # Scan for explicit kwarg patterns from hints_text (high confidence maintainer suggestions)
            if hints_text:
                for m in cls.KWARG_ASSIGN_PATTERN.finditer(hints_text):
                    p_name, p_val = m.group(1), m.group(2)
                    if not is_valid_param_name(p_name):
                        continue
                    if p_name.lower() in [f.lower() for f in contract.forbidden_parameters]:
                        continue

                    p_type = "bool" if p_val in ("True", "False") else ("str" if p_val.startswith(("'", '"')) else "Any")
                    default_val = "True" if p_val in ("False", "True") else p_val
                    observed_params[p_name] = APIParameter(
                        name=p_name,
                        type_annotation=p_type,
                        default_value=default_val,
                        origin="HINTS_TEXT",
                    )

            # Scan for parameter mentions in prose when accompanied by param/argument keyword
            for m in cls.PARAM_HINT_PATTERN.finditer(combined_text):
                p_name = m.group(1) or m.group(2)
                if not p_name or not is_valid_param_name(p_name):
                    continue
                if p_name.lower() in [f.lower() for f in contract.forbidden_parameters]:
                    continue

                if p_name not in observed_params:
                    # Require that the parameter is referenced in hints or near target symbol
                    if (hints_text and p_name in hints_text) or (target_symbol and target_symbol in combined_text):
                        observed_params[p_name] = APIParameter(
                            name=p_name,
                            type_annotation="Any",
                            default_value="True" if "bool" in combined_text.lower() else None,
                            origin="HINTS_TEXT" if hints_text and p_name in hints_text else "ISSUE_EXPLICIT",
                        )

            # Scan for full call examples matching target_symbol: e.g. app.config.from_file(..., text=False)
            for m in cls.KWARG_CALL_PATTERN.finditer(combined_text):
                call_sym, kw_name, kw_val = m.group(1), m.group(2), m.group(3)
                if not is_valid_param_name(kw_name) or kw_name.lower() in [f.lower() for f in contract.forbidden_parameters]:
                    continue
                # STRICT: Only accept if the call matches target_symbol!
                if target_symbol and target_symbol in call_sym:
                    contract.raw_call_examples.append(m.group(0))
                    if kw_name not in observed_params:
                        kw_type = "bool" if kw_val in ("True", "False") else "Any"
                        observed_params[kw_name] = APIParameter(
                            name=kw_name,
                            type_annotation=kw_type,
                            default_value="True" if kw_val == "False" else None,
                            origin="TEST_OBSERVED",
                        )

            for p in observed_params.values():
                if not contract.get_parameter(p.name):
                    contract.parameters.append(p)

        # 3. Extract Exception Contracts
        for m in cls.CATCH_EXCEPTION_PATTERN.finditer(combined_text):
            exc = m.group(1).strip()
            if exc not in contract.handled_exceptions:
                contract.handled_exceptions.append(exc)

        for m in cls.RAISE_EXCEPTION_PATTERN.finditer(combined_text):
            exc = m.group(1).strip()
            if exc not in contract.expected_exceptions:
                contract.expected_exceptions.append(exc)

        # 4. Standard & Domain Invariants
        contract.behavioral_invariants.append("Preserve backward compatibility: existing callers with default arguments must succeed.")
        if any(p.name == "text" for p in contract.parameters):
            contract.behavioral_invariants.append("When `text` is True (or omitted/default), open file in text mode ('r'). When `text` is False, open file in binary mode ('rb').")
        elif any(kw in combined_text.lower() for kw in ("binary", "text", "mode", "bytes")):
            contract.behavioral_invariants.append("Ensure binary vs text stream compatibility matching parameter specification.")

        return contract

    @classmethod
    def _enrich_from_existing_ast(cls, contract: APIContract, source: str, target_symbol: str) -> None:
        """Inspects existing AST to extract baseline parameters and invariants."""
        try:
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == target_symbol:
                    # Baseline parameters
                    for arg in node.args.args:
                        if arg.arg in ("self", "cls"):
                            continue
                        if not contract.get_parameter(arg.arg):
                            contract.parameters.append(
                                APIParameter(
                                    name=arg.arg,
                                    type_annotation=ast.unparse(arg.annotation) if arg.annotation else "",
                                    origin="EXISTING_AST",
                                )
                            )
                    break
        except Exception:
            pass
