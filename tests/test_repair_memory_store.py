"""Unit tests for CrossTaskRepairMemory store."""
from __future__ import annotations

import tempfile
from pathlib import Path
from patchforge.memory.episode import RepairEpisode, save_repair_episode
from patchforge.memory.store import CrossTaskRepairMemory


def test_cross_task_repair_memory():
    with tempfile.TemporaryDirectory() as tmpdir:
        ep1 = RepairEpisode(
            instance_id="requests-1",
            repo="psf/requests",
            resolved=True,
            target={"symbol": "Session.merge_environment_settings"},
            diagnosis={"cause": "proxies dropped in merge", "repair_strategy": "preserve environment proxies"},
            patch_text="def merge_environment_settings(...): pass",
        )
        save_repair_episode(ep1, directory=tmpdir)

        mem = CrossTaskRepairMemory(episodes_dir=tmpdir)
        assert len(mem.episodes) == 1

        hits = mem.query_similar_episodes(
            repo="psf/requests",
            query_text="proxies merge environment settings",
            target_symbol="Session.merge_environment_settings",
            top_k=2,
        )
        assert len(hits) == 1
        assert hits[0].instance_id == "requests-1"
        assert hits[0].similarity_score > 0.5

        ctx = mem.format_memory_context("psf/requests", "proxies merge", "merge_environment_settings")
        assert "RELEVANT PAST REPAIR EXPERIENCES" in ctx
        assert "requests-1" in ctx
