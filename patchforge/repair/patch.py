"""A candidate patch tied to exactly one hypothesis."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Patch:
    hypothesis_id: str = ""
    files_changed: list[str] = field(default_factory=list)
    symbols_changed: list[str] = field(default_factory=list)
    patch_text: str = ""
    reasoning: str = ""
    expected_test_effect: str = ""
    valid: bool = False
    error: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    new_contents: dict = field(default_factory=dict)

    match_tier: str = "EXACT"
    ast_repair_used: bool = False
    is_refinement: bool = False

    def to_dict(self) -> dict:
        return {
            "hypothesis_id": self.hypothesis_id,
            "files_changed": self.files_changed,
            "symbols_changed": self.symbols_changed,
            "patch_text": self.patch_text,
            "reasoning": self.reasoning,
            "expected_test_effect": self.expected_test_effect,
            "valid": self.valid,
            "error": self.error,
            "match_tier": self.match_tier,
            "ast_repair_used": self.ast_repair_used,
            "is_refinement": self.is_refinement,
            "tokens": {"input": self.input_tokens, "output": self.output_tokens},
            "cost_usd": self.cost_usd,
        }
