"""Cross-Task Repair Memory: indexes and recalls historical RepairEpisodes to guide diagnosis and repair."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from enum import Enum
import hashlib
from typing import Any, Dict, List, Optional, Set

from patchforge.memory.episode import RepairEpisode

logger = logging.getLogger(__name__)


class MemoryMode(str, Enum):
    FROZEN = "FROZEN"      # Pinned snapshot: ignores episodes recorded during the current run
    ONLINE = "ONLINE"      # Dynamic: newly recorded episodes immediately enter retrieval pool
    DISABLED = "DISABLED"  # No cross-task exemplars supplied


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

    def __init__(
        self,
        episodes_dir: str = "results/episodes",
        mode: MemoryMode | str = MemoryMode.FROZEN,
        resolved_only_default: bool = True,
    ):
        self.episodes_dir = Path(episodes_dir)
        self.mode = MemoryMode(mode) if isinstance(mode, str) else mode
        self.resolved_only_default = resolved_only_default
        self.episodes: List[RepairEpisode] = []
        self._initial_snapshot_ids: List[str] = []
        self._snapshot_content_hashes: Dict[str, str] = {}
        if self.mode != MemoryMode.DISABLED:
            self._load_episodes()

    def _load_episodes(self):
        if not self.episodes_dir.exists():
            return
        for f in sorted(self.episodes_dir.glob("*.json")):
            try:
                raw_bytes = f.read_bytes()
                data = json.loads(raw_bytes.decode("utf-8", errors="replace"))
                ep = RepairEpisode.from_dict(data)
                self.episodes.append(ep)
                self._initial_snapshot_ids.append(ep.instance_id)
                self._snapshot_content_hashes[ep.instance_id] = hashlib.sha256(raw_bytes).hexdigest()[:16]
            except Exception as e:
                logger.debug(f"Failed to load episode {f}: {e}")

    @property
    def snapshot_fingerprint(self) -> str:
        """Deterministic fingerprint of the loaded memory snapshot."""
        if not self._initial_snapshot_ids:
            return "empty"
        data = ",".join(f"{k}:{self._snapshot_content_hashes.get(k, '')}" for k in sorted(self._initial_snapshot_ids)).encode("utf-8")
        return hashlib.sha256(data).hexdigest()[:16]

    def record_episode(self, episode: RepairEpisode):
        """Persists episode. In ONLINE mode, adds to retrieval pool; in FROZEN mode, keeps retrieval pool frozen."""
        from patchforge.memory.episode import save_repair_episode
        try:
            save_repair_episode(episode, episodes_dir=str(self.episodes_dir))
        except Exception as e:
            logger.debug(f"Failed to save episode to disk: {e}")

        if self.mode == MemoryMode.ONLINE:
            self.episodes.append(episode)

    def query_similar_episodes(
        self,
        repo: str,
        query_text: str,
        target_symbol: str = "",
        top_k: int = 3,
        resolved_only: Optional[bool] = None,
        current_instance_id: str = "",
    ) -> List[RetrievedMemoryExemplar]:
        """Finds most relevant historical repair episodes."""
        if self.mode == MemoryMode.DISABLED or not self.episodes:
            return []

        use_resolved_only = self.resolved_only_default if resolved_only is None else resolved_only

        q_tokens = set(re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", query_text.lower()))
        exemplars: List[RetrievedMemoryExemplar] = []

        for ep in self.episodes:
            if current_instance_id and ep.instance_id == current_instance_id:
                continue
            if use_resolved_only and not ep.resolved:
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

    def retrieve_with_provenance(
        self,
        repo: str,
        query_text: str,
        target_symbol: str = "",
        current_instance_id: str = "",
        top_k: int = 2,
    ) -> Tuple[str, Dict[str, Any]]:
        """Retrieves exemplars and returns both prompt context and detailed provenance."""
        exemplars = self.query_similar_episodes(
            repo=repo,
            query_text=query_text,
            target_symbol=target_symbol,
            top_k=top_k,
            current_instance_id=current_instance_id,
        )
        context_str = ""
        if exemplars:
            lines = ["### RELEVANT PAST REPAIR EXPERIENCES (CROSS-TASK MEMORY):"]
            for ex in exemplars:
                lines.append(ex.format_for_prompt())
            context_str = "\n".join(lines) + "\n"

        provenance = {
            "mode": self.mode.value,
            "memory_mode": self.mode.value,
            "snapshot_fingerprint": self.snapshot_fingerprint,
            "eligible_count": len(self.episodes),
            "eligible_snapshot_ids": sorted(list(self._initial_snapshot_ids)),
            "eligible_episode_ids": sorted(list(self._initial_snapshot_ids)),
            "retrieved_episode_ids": [ex.instance_id for ex in exemplars],
            "exemplars_included_in_prompt": [ex.instance_id for ex in exemplars],
            "prompt_included_episode_ids": [ex.instance_id for ex in exemplars],
            "resolved_only_policy": self.resolved_only_default,
            "current_instance_id": current_instance_id,
            "current_instance_excluded": current_instance_id in self._initial_snapshot_ids,
        }
        return context_str, provenance

    def format_memory_context(
        self,
        repo: str,
        query_text: str,
        target_symbol: str = "",
        current_instance_id: str = "",
    ) -> str:
        ctx, _ = self.retrieve_with_provenance(
            repo=repo,
            query_text=query_text,
            target_symbol=target_symbol,
            current_instance_id=current_instance_id,
            top_k=2,
        )
        return ctx
