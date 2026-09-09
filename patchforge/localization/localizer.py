"""Localization orchestrator (Checkpoint 4). Does NOT replace Agentless.

Pipeline: issue -> Agentless candidates -> RepoGraph expansion ->
evidence aggregation -> deterministic ranking. Returns multiple
Candidates, each traceable to its Evidence.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from patchforge.integrations.agentless import AgentlessLocalization
from patchforge.integrations.repograph import RepoGraphAdapter
from patchforge.issue.problem import Problem
from patchforge.localization.candidate import Candidate
from patchforge.retrieval import ranking as R
from patchforge.retrieval.evidence import Evidence, EvidenceType
from patchforge.retrieval.retriever import Retriever

_SYMBOL_RE = re.compile(r"^\s*(?:function|class|method)\s*:\s*(\w+)")


def mentions(text: str, symbol: str) -> bool:
    """Word-boundary match: avoids 'ok' matching inside 'hooks'."""
    if not symbol:
        return False
    return re.search(r"\b" + re.escape(symbol.lower()) + r"\b", text) is not None


def parse_symbols(entries) -> list[str]:
    """Agentless related/edit loc entries embed 'function: X' / 'class: Y' lines."""
    if isinstance(entries, str):
        entries = [entries]
    names: list[str] = []
    for entry in entries or []:
        for line in str(entry).splitlines():
            m = _SYMBOL_RE.match(line)
            if m and m.group(1) not in names:
                names.append(m.group(1))
    return names


@dataclass
class Localizer:
    repograph: RepoGraphAdapter
    retriever: Retriever | None = None
    top_k: int = 7
    max_per_file: int = 3

    def localize(
        self,
        problem: Problem,
        agentless: AgentlessLocalization,
        graph_pkl: str,
    ) -> list[Candidate]:
        issue_text = (problem.problem_statement + "\n" + problem.hints_text).lower()
        test_text = " ".join(problem.fail_to_pass + problem.pass_to_pass).lower()
        by_key: dict[tuple[str, str], Candidate] = {}
        seq_of: dict[tuple[str, str], int] = {}
        order = 0

        def get(file: str, symbol: str) -> Candidate:
            nonlocal order
            key = (file, symbol)
            if key not in by_key:
                by_key[key] = Candidate(file=file, symbol=symbol)
                seq_of[key] = order
                order += 1
            return by_key[key]

        n = max(1, len(agentless.found_files))
        file_scores = {
            file: (len(agentless.found_files) - rank) / n
            for rank, file in enumerate(agentless.found_files)
        }

        def inherit_file(c: Candidate) -> None:
            """A symbol in an Agentless-ranked file inherits file evidence."""
            fs = file_scores.get(c.file, 0.0)
            if fs <= 0:
                return
            c.score += R.W_AGENT_FILE * fs * 0.5
            c.evidence.append(Evidence(
                source="agentless", type=EvidenceType.SYMBOL_MATCH.value,
                file=c.file, symbol=c.symbol, relevance=round(fs, 3),
                explanation="symbol lives in Agentless-ranked file"))

        for file, score in file_scores.items():
            c = get(file, "")
            c.score += R.W_AGENT_FILE * score * 0.5
            c.evidence.append(Evidence(
                source="agentless", type=EvidenceType.SYMBOL_MATCH.value,
                file=file, relevance=round(score, 3),
                explanation="Agentless file-level hit (no symbol yet, half weight)"))
            if file.lower() in issue_text or file.split("/")[-1].lower() in issue_text:
                c.score += R.W_ISSUE_MENTION
                c.evidence.append(Evidence(
                    source="issue", type=EvidenceType.ISSUE_TEXT.value,
                    file=file, relevance=R.W_ISSUE_MENTION,
                    explanation="file path mentioned in issue text"))

        for file, entries in (agentless.related_locs or {}).items():
            for symbol in parse_symbols(entries):
                c = get(file, symbol)
                c.score += R.W_AGENT_RELATED
                c.evidence.append(Evidence(
                    source="agentless", type=EvidenceType.SEMANTIC_MATCH.value,
                    file=file, symbol=symbol, relevance=R.W_AGENT_RELATED,
                    explanation="Agentless related-element localization"))
                inherit_file(c)
                self._enrich_graph(c, graph_pkl)
                if mentions(issue_text, symbol):
                    c.score += R.W_ISSUE_MENTION
                    c.evidence.append(Evidence(
                        source="issue", type=EvidenceType.ISSUE_TEXT.value,
                        file=file, symbol=symbol, relevance=R.W_ISSUE_MENTION,
                        explanation="symbol mentioned in issue text"))
                if mentions(test_text, symbol):
                    c.score += R.W_TEST_MENTION
                    c.evidence.append(Evidence(
                        source="issue", type=EvidenceType.TEST_REFERENCE.value,
                        file=file, symbol=symbol, relevance=R.W_TEST_MENTION,
                        explanation="symbol mentioned in FAIL_TO_PASS/PASS_TO_PASS ids"))

        for file, entries in (agentless.edit_locs or {}).items():
            for symbol in parse_symbols(entries):
                c = get(file, symbol)
                c.score += R.W_AGENT_EDIT_LOC
                c.evidence.append(Evidence(
                    source="agentless", type=EvidenceType.SEMANTIC_MATCH.value,
                    file=file, symbol=symbol, relevance=R.W_AGENT_EDIT_LOC,
                    explanation="Agentless fine-grain edit location"))
                inherit_file(c)
                self._enrich_graph(c, graph_pkl)

        self._add_issue_symbols(problem, agentless, issue_text, test_text, get, graph_pkl)

        ranked = sorted(
            by_key.values(),
            key=lambda c: (-c.score, 0 if c.symbol else 1, seq_of[(c.file, c.symbol)]),
        )
        for c in ranked:
            c.score = round(c.score, 3)
        diversified: list[Candidate] = []
        per_file: dict[str, int] = {}
        for c in ranked:
            if per_file.get(c.file, 0) >= self.max_per_file:
                continue
            per_file[c.file] = per_file.get(c.file, 0) + 1
            diversified.append(c)
            if len(diversified) >= self.top_k:
                break
        return diversified

    def _add_issue_symbols(self, problem, agentless, issue_text, test_text, get, graph_pkl) -> None:
        """Symbols defined in Agentless files AND named in the issue/tests.

        Closes the gap when Agentless surfaces the right file but not the
        right symbol (cf. requests-863: models.py found, register_hook missed).
        """
        if self.retriever is None:
            return
        for rank, file in enumerate(agentless.found_files):
            file_score = (len(agentless.found_files) - rank) / max(1, len(agentless.found_files))
            try:
                defined = self.retriever.file_symbols(file)
            except Exception:
                continue
            for symbol in defined:
                if not mentions(issue_text, symbol) and not mentions(test_text, symbol):
                    continue
                c = get(file, symbol)
                if any(e.source == "issue-symbol" for e in c.evidence):
                    continue
                c.score += R.W_AGENT_FILE * file_score * 0.5 + R.W_ISSUE_MENTION
                c.evidence.append(Evidence(
                    source="issue-symbol", type=EvidenceType.ISSUE_TEXT.value,
                    file=file, symbol=symbol, relevance=R.W_ISSUE_MENTION,
                    explanation="symbol defined in Agentless file and named in issue/tests"))
                self._enrich_graph(c, graph_pkl)

    def _enrich_graph(self, candidate: Candidate, graph_pkl: str) -> None:
        if not candidate.symbol or not graph_pkl:
            return
        if any(e.source == "repograph" for e in candidate.evidence):
            return
        ctx = self.repograph.related_symbols(graph_pkl, candidate.symbol)
        if ctx.related:
            candidate.score += R.W_GRAPH_NEIGHBOR
            candidate.evidence.append(Evidence(
                source="repograph", type=EvidenceType.GRAPH_RELATION.value,
                file=candidate.file, symbol=candidate.symbol,
                relevance=R.W_GRAPH_NEIGHBOR,
                explanation=f"graph neighbors: {', '.join(ctx.related[:6])}"))
            candidate.evidence.extend(ctx.evidence[:6])
