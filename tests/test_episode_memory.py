"""Unit tests for RepairEpisode memory data collection."""
from __future__ import annotations

import json
from pathlib import Path

from patchforge.memory.episode import RepairEpisode, save_repair_episode


def test_repair_episode_serialization(tmp_path):
    ep = RepairEpisode(
        instance_id="psf__requests-863",
        repo="psf/requests",
        base_commit="a0df2cbb",
        model_name="qwen2.5-coder:7b",
        target={"file_path": "requests/models.py", "symbol": "register_hook"},
        state_flow_summary="=== STATE & CONTROL FLOW DIAGNOSTICS ===\nTarget: register_hook",
        resolved=True,
        patch_applied=True,
        patch_valid=True,
        failure_class="RESOLVED",
        runtime_s=120.5,
        f2p_passed=4,
        f2p_total=4,
        p2p_passed=60,
        p2p_total=60,
        refinement_cycles=0,
        patch_text="### requests/models.py\n<<<<<<< SEARCH\nfoo\n=======\nbar\n>>>>>>> REPLACE",
    )

    d = ep.to_dict()
    assert d["instance_id"] == "psf__requests-863"
    assert d["resolved"] is True
    assert d["f2p_passed"] == 4

    # Save to disk
    ep_file = save_repair_episode(ep, directory=str(tmp_path))
    assert Path(ep_file).exists()

    loaded_raw = json.loads(Path(ep_file).read_text(encoding="utf-8"))
    ep_loaded = RepairEpisode.from_dict(loaded_raw)
    assert ep_loaded.instance_id == ep.instance_id
    assert ep_loaded.resolved == ep.resolved
    assert ep_loaded.state_flow_summary == ep.state_flow_summary
