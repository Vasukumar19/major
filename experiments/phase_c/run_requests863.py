"""CP8 dev run: full PatchForge trajectory on psf__requests-863 from base commit."""
import sys

sys.path.insert(0, ".")

from patchforge.core.config import PatchForgeConfig
from patchforge.core.orchestrator import Orchestrator

cfg = PatchForgeConfig(
    default_model="openai/gpt-4o-mini",
    max_hypotheses=3,
    max_patch_attempts=3,
    max_task_seconds=1500,  # dev budget; spec target is 600 (see README)
    workspace=".",
)
ep = Orchestrator(cfg, workspace=".").run_instance("psf__requests-863")
print("STATUS:", ep.status)
print("FAILURE:", ep.failure_stage)
print("ATTEMPTS:", len(ep.attempts))
for a in ep.attempts:
    t = (a["tests"].get("eval") or {}) if isinstance(a["tests"], dict) else {}
    print(f"  #{a['hypothesis_id']} {a['failure_class']} "
          f"F2P={(t.get('fail_to_pass') or {}).get('passed', '?')}")
print("TOKENS:", ep.input_tokens, ep.output_tokens)
print("COST:", ep.cost_usd)
print("RUNTIME_S:", ep.runtime_s)
