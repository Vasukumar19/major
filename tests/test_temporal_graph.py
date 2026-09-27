"""Unit tests for Temporal Repository Graph."""
from __future__ import annotations

import tempfile
import subprocess
from pathlib import Path
from patchforge.repository_intelligence.temporal_graph import (
    TemporalRepositoryGraph,
    CommitRecord,
    SymbolTemporalProfile,
)


def test_temporal_graph_profile():
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_p = Path(tmpdir)
        # Initialize dummy git repo
        subprocess.run(["git", "init"], cwd=tmpdir, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "Tester"], cwd=tmpdir, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmpdir, capture_output=True)

        f = repo_p / "module.py"
        f.write_text("def target_fn():\n    return 1\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=tmpdir, capture_output=True)
        subprocess.run(["git", "commit", "-m", "fix: fix bug in target_fn"], cwd=tmpdir, capture_output=True)

        tg = TemporalRepositoryGraph(tmpdir)
        tg.build(max_commits=10)

        assert len(tg.commits) >= 1
        prof = tg.get_symbol_profile("module.py", "target_fn")
        assert prof.file_path == "module.py"
        assert prof.total_commits >= 1
        assert prof.bug_fix_commits >= 1
        assert prof.churn_score > 0.0
        assert "Temporal Profile" in prof.format_summary()
