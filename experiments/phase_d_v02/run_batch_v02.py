"""Batch runner for PatchForge v0.2 full cohort evaluation (resume-safe)."""
import argparse
import subprocess
import sys
import time

COHORT_INSTANCES = [
    # Requests
    "psf__requests-1963",
    "psf__requests-2148",
    "psf__requests-2674",
    # Flask
    "pallets__flask-4045",
    "pallets__flask-4992",
    "pallets__flask-5063",
    # Pytest
    "pytest-dev__pytest-5692",
    "pytest-dev__pytest-5221",
    "pytest-dev__pytest-5413",
    # Django
    "django__django-10914",
    "django__django-11039",
    "django__django-13401",
    "django__django-13447",
    # SymPy
    "sympy__sympy-12419",
    "sympy__sympy-13437",
    "sympy__sympy-15609",
]

def main():
    print(f"==================================================", flush=True)
    print(f"Starting PatchForge v0.2 Batch ({len(COHORT_INSTANCES)} tasks)", flush=True)
    print(f"==================================================", flush=True)

    start_time = time.time()
    for idx, instance_id in enumerate(COHORT_INSTANCES, 1):
        print(f"\n[{idx}/{len(COHORT_INSTANCES)}] Running v0.2 on {instance_id}...", flush=True)
        t0 = time.time()
        res = subprocess.run([sys.executable, "experiments/phase_d_v02/run_arm_v02.py", "--instance", instance_id])
        elapsed = round(time.time() - t0, 1)
        print(f"[{idx}/{len(COHORT_INSTANCES)}] Finished {instance_id} in {elapsed}s (code {res.returncode})", flush=True)

    total_elapsed = round(time.time() - start_time, 1)
    print(f"\n==================================================", flush=True)
    print(f"PatchForge v0.2 Batch completed in {total_elapsed}s", flush=True)
    print(f"==================================================", flush=True)

if __name__ == "__main__":
    main()
