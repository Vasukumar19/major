"""Probe Injector: instruments source files temporarily with sentinel observation probes."""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Dict, List, Optional

from patchforge.experiment.probe import ProbeSpecification, ProbeType

logger = logging.getLogger(__name__)


class ProbeInjector:
    """Non-destructive source instrumentation for dynamic sandboxed experiments."""

    SENTINEL_PREFIX = "__PF_PROBE__"

    def __init__(self, repo_dir: str):
        self.repo_dir = Path(repo_dir)
        self.original_contents: Dict[str, str] = {}

    def _generate_probe_code(self, probe: ProbeSpecification, indent: str) -> str:
        """Generates minimal, non-throwing python instrumentation snippet."""
        pid = probe.probe_id
        if probe.probe_type == ProbeType.VARIABLE_VALUE and probe.variable_name:
            var = probe.variable_name
            return f"{indent}try: print('{self.SENTINEL_PREFIX}:{pid}:' + repr({var}))\n{indent}except Exception as _pe: print('{self.SENTINEL_PREFIX}:{pid}:ERR:' + repr(_pe))\n"
        elif probe.probe_type == ProbeType.FUNCTION_ENTRY:
            return f"{indent}print('{self.SENTINEL_PREFIX}:{pid}:ENTER')\n"
        elif probe.probe_type == ProbeType.FUNCTION_EXIT:
            return f"{indent}print('{self.SENTINEL_PREFIX}:{pid}:EXIT')\n"
        elif probe.probe_type == ProbeType.BRANCH_DECISION:
            expr = probe.expression or "True"
            return f"{indent}print('{self.SENTINEL_PREFIX}:{pid}:BRANCH:' + repr({expr}))\n"
        else:
            expr = probe.expression or probe.variable_name or "None"
            return f"{indent}try: print('{self.SENTINEL_PREFIX}:{pid}:' + repr({expr}))\n{indent}except Exception as _pe: pass\n"

    def inject_probes(self, probes: List[ProbeSpecification]) -> bool:
        """Injects temporary sentinel probes into target files, backing up originals."""
        # Group by file
        by_file: Dict[str, List[ProbeSpecification]] = {}
        for p in probes:
            by_file.setdefault(p.file_path.replace("\\", "/"), []).append(p)

        for rel_file, file_probes in by_file.items():
            full_path = self.repo_dir / rel_file
            if not full_path.exists():
                logger.warning(f"Probe target file does not exist: {full_path}")
                continue

            content = full_path.read_text(encoding="utf-8", errors="replace")
            if rel_file not in self.original_contents:
                self.original_contents[rel_file] = content

            lines = content.splitlines(keepends=True)
            # Sort probes by line descending so insertions don't shift earlier lines
            sorted_probes = sorted(file_probes, key=lambda x: x.target_line, reverse=True)

            for probe in sorted_probes:
                line_idx = max(0, min(len(lines) - 1, probe.target_line - 1))
                target_line = lines[line_idx]
                indent_match = re.match(r"^(\s*)", target_line)
                indent = indent_match.group(1) if indent_match else ""

                probe_code = self._generate_probe_code(probe, indent)
                lines.insert(line_idx, probe_code)

            full_path.write_text("".join(lines), encoding="utf-8")

        return True

    def restore_all(self):
        """Restores all instrumented files to their original pristine state."""
        for rel_file, orig_text in self.original_contents.items():
            full_path = self.repo_dir / rel_file
            try:
                full_path.write_text(orig_text, encoding="utf-8")
            except Exception as e:
                logger.error(f"Failed to restore probe file {rel_file}: {e}")
        self.original_contents.clear()
