"""Generate 2-3 competing root-cause hypotheses from evidence.

One LLM call per hypothesis slot would waste context; instead a single
prompt asks for competing hypotheses, then generate_many() is used only
when the first response is unparseable (bounded sequential retries).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from patchforge.issue.problem import Problem
from patchforge.localization.candidate import Candidate
from patchforge.models.provider import ModelProvider
from patchforge.reasoning.hypothesis import Hypothesis
from patchforge.retrieval.evidence import Evidence, EvidenceType
from patchforge.retrieval.retriever import Retriever

SYSTEM = (
    "You are a careful debugging assistant. Propose competing root-cause "
    "hypotheses grounded ONLY in the evidence given. Reply with a JSON list."
)


def build_prompt(problem: Problem, candidates: list[Candidate], code: list[str]) -> str:
    lines = [
        "ISSUE:",
        problem.problem_statement.strip()[:3000],
        "",
        "LOCALIZED CANDIDATES (file | symbol | score | why):",
    ]
    for c in candidates[:5]:
        why = "; ".join(e.explanation for e in c.evidence[:3])
        lines.append(f"- {c.file} | {c.symbol or '(file)'} | {c.score} | {why}")
    lines += ["", "RELEVANT CODE EXCERPTS:"]
    lines.extend(excerpt[:2500] for excerpt in code[:4])
    lines += ["",
              "FAILING TESTS: " + ", ".join(problem.fail_to_pass[:8]),
              "",
              "Propose EXACTLY 3 competing root-cause hypotheses as a JSON list. "
              "The hypotheses must target DIFFERENT primary files "
              "(different affected_files[0]) so competing locations are covered. "
              "Each item: {\"id\": \"H1\", \"description\": \"...\", "
              "\"affected_files\": [...], \"affected_symbols\": [...], "
              "\"confidence\": 0.0-1.0, \"expected_behavior\": \"...\", "
              "\"support_refs\": [{\"file\": \"...\", \"symbol\": \"...\"}], "
              "\"contradicting\": \"...\"}. "
              "support_refs must cite ONLY files/symbols from the candidates above."]
    return "\n".join(lines)


def parse_hypotheses(text: str) -> list[dict]:
    """Extract a JSON list from model output (tolerates code fences)."""
    m = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.DOTALL)
    raw = m.group(1) if m else text[text.find("["): text.rfind("]") + 1]
    items = json.loads(raw)
    if not isinstance(items, list):
        raise ValueError("hypotheses JSON is not a list")
    return items


@dataclass
class HypothesisGenerator:
    provider: ModelProvider
    retriever: Retriever | None = None
    max_hypotheses: int = 3

    def code_context(self, candidates: list[Candidate]) -> list[str]:
        if self.retriever is None:
            return []
        excerpts = []
        for c in candidates[:4]:
            if c.symbol:
                ctx = self.retriever.symbol_context(c.file, c.symbol)
            else:
                ctx = None
            if ctx is not None and ctx.text:
                excerpts.append(f"### {c.file} :: {c.symbol}\n" + ctx.text[:2500])
            elif not c.symbol:
                text = self.retriever.read_file(c.file, max_lines=120)
                if text:
                    excerpts.append(f"### {c.file}\n" + text[:2500])
        return excerpts

    def generate(self, problem: Problem, candidates: list[Candidate]) -> list[Hypothesis]:
        prompt = build_prompt(problem, candidates, self.code_context(candidates))
        last_error: Exception | None = None
        for attempt in range(2):
            temperature = 0.3 if attempt == 0 else 0.8
            gen = self.provider.generate_one(
                prompt, system=SYSTEM, max_tokens=1500, temperature=temperature)
            try:
                items = parse_hypotheses(gen.text)
                return [self._to_hypothesis(d, i, candidates)
                        for i, d in enumerate(items[: self.max_hypotheses])]
            except (ValueError, KeyError) as e:
                last_error = e
        raise ValueError(f"Could not parse hypotheses: {last_error}")

    def _to_hypothesis(self, d: dict, i: int, candidates: list[Candidate]) -> Hypothesis:
        hid = str(d.get("id", f"H{i + 1}"))
        files = [str(f) for f in d.get("affected_files", [])]
        symbols = [str(s) for s in d.get("affected_symbols", [])]
        supporting = self._link_support(d.get("support_refs", []), candidates)
        contra_text = str(d.get("contradicting", ""))
        contradicting = ([Evidence(source="model", type=EvidenceType.CODE_BEHAVIOR.value,
                                   explanation=contra_text)] if contra_text else [])
        try:
            confidence = max(0.0, min(1.0, float(d.get("confidence", 0.5))))
        except (TypeError, ValueError):
            confidence = 0.5
        return Hypothesis(
            id=hid, description=str(d.get("description", "")),
            supporting_evidence=supporting, contradicting_evidence=contradicting,
            affected_files=files, affected_symbols=symbols,
            confidence=confidence,
            expected_behavior=str(d.get("expected_behavior", "")),
        )

    @staticmethod
    def _link_support(refs: list, candidates: list[Candidate]) -> list[Evidence]:
        """Link cited refs to real candidate evidence (traceability)."""
        pool = [e for c in candidates for e in c.evidence]
        linked = []
        for ref in refs or []:
            f = str(ref.get("file", ""))
            s = str(ref.get("symbol", ""))
            matches = [e for e in pool if e.file == f and (not s or e.symbol == s)]
            if matches:
                linked.append(max(matches, key=lambda e: e.relevance))
            else:
                linked.append(Evidence(source="model", type=EvidenceType.CODE_BEHAVIOR.value,
                                       file=f, symbol=s,
                                       relevance=0.3,
                                       explanation="cited by hypothesis, no candidate evidence"))
        return linked
