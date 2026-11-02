"""Report configured PatchForge service readiness without starting services."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from patchforge.runtime import RuntimeEnvironment


def main() -> int:
    environment = RuntimeEnvironment.from_environment()
    probes = [environment.probe_ollama(), environment.probe_docker()]
    for probe in probes:
        state = "READY" if probe.ready else "NOT READY"
        print(f"{probe.name}: {state} — {probe.detail}")
    return 0 if all(probe.ready for probe in probes) else 1


if __name__ == "__main__":
    raise SystemExit(main())
