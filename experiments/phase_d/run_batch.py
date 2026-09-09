"""Sequential batch runner for Phase D arms (resume-safe)."""
import argparse
import subprocess
import sys
import time

COHORT_INSTANCES = [
    # Requests (fast)
    "psf__requests-1963",
    "psf__requests-2148",
    "psf__requests-2674",
    # Flask (fast)
    "pallets__flask-4045",
    "pallets__flask-4992",
    "pallets__flask-5063",
    # Pytest (medium)
    "pytest-dev__pytest-5692",
    "pytest-dev__pytest-5221",
    "pytest-dev__pytest-5413",
    # Django (large)
    "django__django-10914",
    "django__django-11039",
    "django__django-13401",
    "django__django-13447",
    # SymPy (large)
    "sympy__sympy-12419",
    "sympy__sympy-13437",
    "sympy__sympy-15609",
]

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--arm", required=True, choices=["A", "B", "C"])
    args = parser.parse_args()

    print(f"Starting Phase D Batch for Arm {args.arm} ({len(COHORT_INSTANCES)} tasks)", flush=True)
    for idx, instance_id in enumerate(COHORT_INSTANCES, 1):
        print(f"\n[{idx}/{len(COHORT_INSTANCES)}] Running Arm {args.arm} on {instance_id}...", flush=True)
        t0 = time.time()
        res = subprocess.run([sys.executable, "experiments/phase_d/run_arm.py", "--arm", args.arm, "--instance", instance_id])
        elapsed = round(time.time() - t0, 1)
        print(f"[{idx}/{len(COHORT_INSTANCES)}] Finished {instance_id} in {elapsed}s (code {res.returncode})", flush=True)

if __name__ == "__main__":
    main()
