"""Cross-Task Repair Memory: indexes and recalls historical RepairEpisodes to guide diagnosis and repair."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from patchforge.memory.episode import RepairEpisode

logger = logging.getLogger(__name__)


@dataclass
class RetrievedMemoryExemplar:
    instance_id: str
    repo: str
    target_symbol: str
    resolved: bool
    repair_strategy: str
    causal_chain: List[str]
    similarity_score: float
    patch_snippet: str = ""

    def format_for_prompt(self) -> str:
        status_str = "RESOLVED" if self.resolved else "PARTIAL"
        lines = [f"Past Repair Experience [{self.instance_id}] ({status_str}, similarity={self.similarity_score:.2f}):"]
        lines.append(f"  - Target Symbol: `{self.target_symbol}`")
        if self.repair_strategy:
            lines.append(f"  - Strategy: {self.repair_strategy}")
        if self.patch_snippet:
            lines.append(f"  - Patch:\n```python\n{self.patch_snippet[:300]}\n```")
        return "\n".join(lines)


class CrossTaskRepairMemory:
    """Indexes and retrieves past repair episodes based on repository, symbol, and symptom matching."""

    def __init__(self, episodes_dir: str = "results/episodes"):
        self.episodes_dir = Path(episodes_dir)
        self.episodes: List[RepairEpisode] = []
        self._load_episodes()

    def _load_episodes(self):
        if not self.episodes_dir.exists():
            return
        for f in self.episodes_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8", errors="replace"))
                ep = RepairEpisode.from_dict(data)
                self.episodes.append(ep)
            except Exception as e:
                logger.debug(f"Failed to load episode {f}: {e}")

    def query_similar_episodes(
        self,
        repo: str,
        query_text: str,
        target_symbol: str = "",
        top_k: int = 3,
        resolved_only: bool = False,
    ) -> List[RetrievedMemoryExemplar]:
        """Finds most relevant historical repair episodes."""
        if not self.episodes:
            return []

        q_tokens = set(re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", query_text.lower()))
        exemplars: List[RetrievedMemoryExemplar] = []

        for ep in self.episodes:
            if resolved_only and not ep.resolved:
                continue

            score = 0.0
            # Same repo bonus
            if ep.repo == repo:
                score += 0.3

            # Target symbol overlap
            ep_sym = ep.target.get("symbol", "")
            if target_symbol and ep_sym:
                if target_symbol.lower() in ep_sym.lower() or ep_sym.lower() in target_symbol.lower():
                    score += 0.4

            # Text overlap with diagnosis / strategy
            diag_text = (
                ep.diagnosis.get("cause", "") + " " + ep.diagnosis.get("repair_strategy", "")
            ).lower()
            diag_tokens = set(re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", diag_text))
            if q_tokens and diag_tokens:
                overlap = len(q_tokens & diag_tokens) / len(q_tokens | diag_tokens)
                score += 0.3 * overlap

            # Resolved bonus
            if ep.resolved:
                score += 0.1

            if score > 0.1:
                exemplars.append(
                    RetrievedMemoryExemplar(
                        instance_id=ep.instance_id,
                        repo=ep.repo,
                        target_symbol=ep_sym,
                        resolved=ep.resolved,
                        repair_strategy=ep.diagnosis.get("repair_strategy", ""),
                        causal_chain=ep.causal_chain,
                        similarity_score=round(score, 3),
                        patch_snippet=ep.patch_text[:400] if ep.patch_text else "",
                    )
                )

        ranked = sorted(exemplars, key=lambda x: x.similarity_score, reverse=True)
        return ranked[:top_k]

    def format_memory_context(
        self,
        repo: str,
        query_text: str,
        target_symbol: str = "",
    ) -> str:
        exemplars = self.query_similar_episodes(repo, query_text, target_symbol, top_k=2)
        if not exemplars:
            return ""
        lines = ["### RELEVANT PAST REPAIR EXPERIENCES (CROSS-TASK MEMORY):"]
        for ex in exemplars:
            lines.append(ex.format_for_prompt())
        return "\n".join(lines) + "\n"
