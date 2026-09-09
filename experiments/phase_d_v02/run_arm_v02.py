"""Phase D v0.2 runner: PatchForge v0.2 with Near-Miss Retention and Fuzzy/AST Patching.

Writes to results/phase_d_v02/C_patchforge/<id>.json.
Phase D (v0.1) results in results/phase_d/ remain immutable.
"""
import argparse
import json
import os
import shutil
import sys
import time

from dotenv import load_dotenv
load_dotenv()

sys.path.insert(0, ".")

from patchforge.core.config import PatchForgeConfig
from patchforge.core.orchestrator import Orchestrator

def record_path(instance_id):
    return os.path.join("results", "phase_d_v02", "C_patchforge", instance_id + ".json")

def load_record(instance_id):
    p = record_path(instance_id)
    if os.path.exists(p):
        try:
            d = json.load(open(p, encoding="utf-8"))
            if d.get("status"):
                return d
        except Exception:
            pass
    return None

def save_record(instance_id, rec):
    p = record_path(instance_id)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    json.dump(rec, open(p, "w", encoding="utf-8"), indent=2)

def run_v02_instance(instance_id):
    t0 = time.time()
    for d in (f"experiments/phase_d_v02/work/{instance_id}", f"experiments/phase_d_v02/{instance_id}",
              f"experiments/phase_c/work/{instance_id}", f"experiments/phase_c/{instance_id}"):
        shutil.rmtree(os.path.abspath(d), ignore_errors=True)
    
    cfg = PatchForgeConfig(default_model="openai/gpt-4o-mini",
                           max_hypotheses=3, max_patch_attempts=3,
                           max_task_seconds=1500, workspace=".")
    
    print(f"RUNNING PatchForge v0.2 on {instance_id}...", flush=True)
    ep = Orchestrator(cfg, workspace=".").run_instance(instance_id)
    d = ep.to_dict()
    
    cands = d.get("localization", {}).get("top_candidates", [])
    f2p = (d.get("tests") or {}).get("fail_to_pass") or {}
    p2p = (d.get("tests") or {}).get("pass_to_pass") or {}
    
    # Check attempts for research telemetry
    attempts = d.get("attempts", [])
    refinement_used = any(a.get("refinement", False) for a in attempts)
    retained_used = any(a.get("retained", False) for a in attempts)
    ast_repair_used = any(a.get("ast_repair_used", False) for a in attempts)
    match_tiers = [a.get("match_tier") for a in attempts if a.get("match_tier")]
    
    rec = {
        "version": "v0.2",
        "instance": instance_id,
        "base_commit": d.get("base_commit", ""),
        "status": d.get("status"),
        "failure_stage": d.get("failure_stage"),
        "localization_top1": (cands[0]["file"] + "::" + cands[0]["symbol"]) if cands else None,
        "localization_topk": [c["file"] + ("::" + c["symbol"] if c.get("symbol") else "") for c in cands],
        "hypotheses_generated": len(d.get("hypotheses", [])),
        "selected_hypothesis": d.get("selected_hypothesis"),
        "patch_attempts": len(attempts),
        "patch_valid": bool((d.get("patch") or {}).get("valid", False)),
        "FAIL_TO_PASS": {"passed": f2p.get("passed", 0), "total": f2p.get("total", 0)},
        "PASS_TO_PASS": {"passed": p2p.get("passed", 0), "total": p2p.get("total", 0)},
        "telemetry": {
            "refinement_used": refinement_used,
            "retained_used": retained_used,
            "ast_repair_used": ast_repair_used,
            "match_tiers": match_tiers,
            "attempts_detail": attempts,
        },
        "input_tokens": d.get("tokens", {}).get("input", 0),
        "output_tokens": d.get("tokens", {}).get("output", 0),
        "total_tokens": d.get("tokens", {}).get("input", 0) + d.get("tokens", {}).get("output", 0),
        "cost_usd": d.get("cost_usd", 0.0),
        "runtime_s": d.get("runtime_seconds", round(time.time() - t0, 1)),
    }
    save_record(instance_id, rec)
    print(f"DONE v0.2 {instance_id}: {rec['status']} F2P={rec['FAIL_TO_PASS']} P2P={rec['PASS_TO_PASS']} Refinement={refinement_used} Cost=${rec['cost_usd']}", flush=True)
    return rec

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--instance", required=True)
    args = parser.parse_args()
    run_v02_instance(args.instance)

if __name__ == "__main__":
    main()
