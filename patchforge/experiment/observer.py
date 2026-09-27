"""Probe Observer: extracts and structures sentinel observations from test logs."""
from __future__ import annotations

import logging
import re
from typing import Dict, List, Optional

from patchforge.experiment.probe import ProbeObservation

logger = logging.getLogger(__name__)


class ProbeObserver:
    """Parses execution output to extract sentinel probe traces."""

    SENTINEL_PATTERN = re.compile(r"__PF_PROBE__:([a-zA-Z0-9_\-]+):(.*)")

    @classmethod
    def parse_output(cls, output_text: str) -> List[ProbeObservation]:
        """Scans test stdout/stderr and extracts all sentinel observations."""
        observations: Dict[str, ProbeObservation] = {}

        for line in output_text.splitlines():
            m = cls.SENTINEL_PATTERN.search(line)
            if m:
                probe_id = m.group(1)
                val_str = m.group(2).strip()

                exc = None
                if val_str.startswith("ERR:"):
                    exc = val_str[4:]

                if probe_id in observations:
                    observations[probe_id].hit_count += 1
                    # Keep latest observation or append
                    observations[probe_id].observed_value = val_str
                else:
                    observations[probe_id] = ProbeObservation(
                        probe_id=probe_id,
                        observed_value=val_str,
                        raw_output=line.strip(),
                        hit_count=1,
                        exception_caught=exc,
                    )

        return list(observations.values())
