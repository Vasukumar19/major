"""Phase D runner: ONE arm x ONE instance per invocation (resume-safe).

Arms (frozen protocols, no system changes):
  A = Agentless localize (3 stages, num_samples=1, gpt-4o-mini) + repair
      (max_samples=1 greedy) + official eval.  [Phase-B recipe]
  B = RepoGraph fork localize (3 stages + --repo_graph, num_samples=1,
      hardcoded gpt-4o) + fork repair (max_samples=1, --repo_graph) +
      official eval. Graph/structure cache staged per run, unstaged after.
  C = PatchForge Orchestrator.run_instance unchanged.

Usage:
  set OPENROUTER_API_KEY, OPENAI_API_KEY, OPENAI_BASE_URL, PYTHONUTF8
  python experiments/phase_d/run_arm.py --arm A --instance <id>

Writes results/phase_d/<ARM>/<id>.json (+ work/logs alongside).
Skips instances that already have a record with a status (resume).
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import traceback

from dotenv import load_dotenv
load_dotenv()

sys.path.insert(0, ".")

from patchforge.core.config import PatchForgeConfig
from patchforge.integrations.agentless import AGENTLESS_MODEL, AgentlessAdapter
from patchforge.integrations.swebench import SWEBenchAdapter
from patchforge.localization.candidate import Candidate
from patchforge.repair.patch import Patch
from patchforge.verification.classifier import classify

ARMS = ("A", "B", "C")
MINI_IN, MINI_OUT = 0.15 / 1_000_000, 0.60 / 1_000_000
GPT4O_IN, GPT4O_OUT = 5.0 / 1_000_000, 15.0 / 1_000_000
HARNESS_TIMEOUT = 180


def record_path(arm, instance_id):
    return os.path.join("results", "phase_d", arm, instance_id + ".json")


def load_record(arm, instance_id):
    p = record_path(arm, instance_id)
    if os.path.exists(p):
        try:
            d = json.load(open(p))
            if d.get("status"):
                return d
        except Exception:
            pass
    return None


def save_record(arm, instance_id, rec):
    p = record_path(arm, instance_id)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    json.dump(rec, open(p, "w"), indent=2)


def fresh_work(*dirs):
    """Remove stale partial-run dirs (repos/graph caches are never touched)."""
    for d in dirs:
        shutil.rmtree(d, ignore_errors=True)


def estimate_cost(in_tokens, out_tokens, arm):
    if arm == "B":
        return in_tokens * GPT4O_IN + out_tokens * GPT4O_OUT
    return in_tokens * MINI_IN + out_tokens * MINI_OUT


def walk_usage(root):
    prompt, completion = 0, 0

    def walk(o):
        nonlocal prompt, completion
        if isinstance(o, dict):
            if set(o) >= {"prompt_tokens", "completion_tokens"}:
                try:
                    prompt += int(o.get("prompt_tokens") or 0)
                    completion += int(o.get("completion_tokens") or 0)
                except (TypeError, ValueError):
                    pass
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    for dirpath, _, files in os.walk(root):
        for fn in files:
            if not fn.endswith(".jsonl"):
                continue
            try:
                for line in open(os.path.join(dirpath, fn), encoding="utf-8", errors="replace"):
                    try:
                        walk(json.loads(line))
                    except ValueError:
                        continue
            except OSError:
                continue
    return prompt, completion


def base_record(instance_id, base_commit):
    return {
        "instance": instance_id, "base_commit": base_commit,
        "status": None, "failure_stage": None,
        "localization_top1": None, "localization_topk": [],
        "hypotheses_generated": 0, "selected_hypothesis": None,
        "patch_attempts": 0, "patch_valid": False,
        "FAIL_TO_PASS": {"passed": 0, "total": 0},
        "PASS_TO_PASS": {"passed": 0, "total": 0},
        "input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
        "cost_usd": 0.0, "cost_estimated": True, "runtime_s": 0.0,
    }


def run_agentless_repair(agentless_dir, loc_file, out_dir, instance_id, fork=False,
                         structures_dir=None):
    """Run repair CLI (upstream or fork). Returns processed-row dict or None."""
    if fork:
        exe = os.path.abspath("third_party/RepoGraph/agentless/repair/repair.py")
        env = dict(os.environ)
        env["PYTHONPATH"] = "." + os.pathsep + "agentless/"
        cwd = os.path.abspath("third_party/RepoGraph")
    else:
        exe = os.path.abspath("third_party/Agentless/agentless/repair/repair.py")
        env = dict(os.environ)
        sep = os.pathsep
        env["PYTHONPATH"] = os.path.abspath("third_party/Agentless") + sep + env.get("PYTHONPATH", "")
        if structures_dir:
            env["PROJECT_FILE_LOC"] = structures_dir
        cwd = os.path.abspath(".")
    base = [sys.executable, exe, "--loc_file", loc_file,
            "--output_folder", out_dir, "--loc_interval", "--top_n", "3",
            "--context_window", "10", "--max_samples", "1",
            "--cot", "--diff_format", "--target_id", instance_id,
            "--num_threads", "1"]
    if not fork:
        base += ["--model", AGENTLESS_MODEL, "--backend", "openai"]
    if fork:
        base += ["--repo_graph"]
    r = subprocess.run(base + ["--gen_and_process"], capture_output=True, text=True,
                       env=env, cwd=cwd, timeout=1800)
    if r.returncode != 0:
        raise RuntimeError("repair gen failed: " + r.stderr[-1500:])
    processed = os.path.join(out_dir, "output_0_processed.jsonl")
    if not fork and not os.path.exists(processed):
        # Upstream usually emits output_0_processed.jsonl during gen_and_process;
        # post-process separately only if it did not.
        r = subprocess.run(base + ["--post_process"], capture_output=True, text=True,
                           env=env, cwd=cwd, timeout=600)
    for fn in ("output_0_processed.jsonl", "output.jsonl"):
        p = os.path.join(out_dir, fn)
        if os.path.exists(p):
            try:
                return json.load(open(p, encoding="utf-8"))
            except Exception:
                continue
    return None


def assemble_patch_text(row):
    if not row:
        return "", [], "no repair output"
    try:
        files = row.get("edited_files") or []
        orig = row.get("original_file_content") or []
        new = row.get("new_file_content") or []
        if isinstance(orig, str):
            orig = [orig]
        if isinstance(new, str):
            new = [new]
        text = AgentlessAdapter.assemble_diff(files, orig, new)
        if not text.strip():
            return "", files, "empty diff"
        return text, files, ""
    except Exception as e:
        return "", [], "assemble error: " + str(e)[:200]


def run_arm_a(instance_id):
    t0 = time.time()
    cfg = PatchForgeConfig()
    sw = SWEBenchAdapter(cfg)
    problem = sw.load_problem(instance_id)
    rec = base_record(instance_id, problem.base_commit)
    work = os.path.abspath(f"results/phase_d/work/A/{instance_id}")
    fresh_work(work)
    os.makedirs(work, exist_ok=True)
    try:
        repo_dir = sw.ensure_checkout(problem, "experiments/phase_d/repos")
        ad = AgentlessAdapter(cfg)
        loc = ad.localize(problem, repo_dir, work + "/agentless")
        rec["localization_top1"] = loc.found_files[0] if loc.found_files else None
        rec["localization_topk"] = loc.found_files
        rdir = work + "/repair"
        os.makedirs(rdir, exist_ok=True)
        row = run_agentless_repair(
            None, work + "/agentless/edit_location_samples/loc_outputs.jsonl",
            rdir, instance_id, structures_dir=work + "/agentless/structures")
        patch_text, files, err = assemble_patch_text(row)
        patch = Patch(hypothesis_id="", files_changed=files, patch_text=patch_text,
                      valid=bool(patch_text.strip()), error=err)
        rec["patch_attempts"] = 1
        rec["patch_valid"] = patch.valid
        if not patch.valid:
            # Same gate as PatchForge's Tester: no Docker eval for invalid patches.
            rec["status"] = "UNRESOLVED"
            rec["failure_stage"] = "PATCH_SYNTAX"
        else:
            preds = sw.write_predictions(instance_id, patch_text, "agentless-phased-A",
                                         work + "/predictions.json")
            ev = sw.evaluate(preds, instance_id, f"phased-A-{instance_id}",
                             timeout=HARNESS_TIMEOUT)
            rec["FAIL_TO_PASS"] = {"passed": ev.fail_to_pass_passed, "total": ev.fail_to_pass_total}
            rec["PASS_TO_PASS"] = {"passed": ev.pass_to_pass_passed, "total": ev.pass_to_pass_total}
            cands = [Candidate(file=f) for f in loc.found_files]
            fc = classify(patch, ev, cands)
            rec["status"] = "RESOLVED" if ev.resolved else (
                "TIMEOUT" if fc.value == "TIMEOUT" else ("INFRA_FAILURE" if fc.value == "INFRA_FAILURE" else "UNRESOLVED"))
            rec["failure_stage"] = None if ev.resolved else fc.value
            err_lower = (ev.error or "").lower()
            if not ev.resolved and ("patch apply failed" in err_lower or "malformed patch" in err_lower):
                # Truncated/malformed diff (e.g. model hit max_tokens): synthesis
                # failure, not infrastructure.
                rec["status"] = "UNRESOLVED"
                rec["failure_stage"] = "PATCH_SYNTAX"
        pin, pout = walk_usage(work + "/agentless")
        rec["input_tokens"], rec["output_tokens"] = pin, pout
        rec["total_tokens"] = pin + pout
        rec["cost_usd"] = round(estimate_cost(pin, pout, "A"), 6)
    except Exception as e:
        rec["status"] = "INFRA_FAILURE"
        rec["failure_stage"] = "INFRA_FAILURE"
        open(work + "/error.log", "w").write(traceback.format_exc()[-3000:])
        rec["error"] = str(e)[:300]
    rec["runtime_s"] = round(time.time() - t0, 1)
    return rec


def run_arm_b(instance_id):
    t0 = time.time()
    cfg = PatchForgeConfig()
    sw = SWEBenchAdapter(cfg)
    problem = sw.load_problem(instance_id)
    rec = base_record(instance_id, problem.base_commit)
    work = os.path.abspath(f"results/phase_d/work/B/{instance_id}")
    fresh_work(work)
    os.makedirs(work, exist_ok=True)
    staged = []
    try:
        from patchforge.integrations.repograph import RepoGraphAdapter
        repo_dir = sw.ensure_checkout(problem, "experiments/phase_d/repos")
        rg = RepoGraphAdapter(cfg)
        cache = os.path.abspath("experiments/phase_d/cache/graphs")
        graph_pkl = rg.ensure_graph(repo_dir, instance_id, cache)
        tags_src = os.path.join(cache, instance_id + ".tags.json")
        if not os.path.exists(tags_src):
            tags = [json.loads(l) for l in
                    open(os.path.join(cache, "tags.json"), encoding="utf-8").splitlines()]
            json.dump(tags, open(tags_src, "w"))
            try:
                os.remove(os.path.join(cache, "tags.json"))
            except OSError:
                pass
        structs_src = os.path.join(work, "structure.json")
        ad = AgentlessAdapter(cfg)
        ad.build_structure(instance_id, problem.repo, problem.base_commit, repo_dir,
                           os.path.dirname(structs_src))
        os.rename(os.path.join(os.path.dirname(structs_src), instance_id + ".json"), structs_src)
        import shutil
        stage_root = os.path.abspath("third_party/RepoGraph/repo_structures")
        stage_graph = os.path.join(stage_root, "graph")
        os.makedirs(stage_graph, exist_ok=True)
        row = json.load(open(structs_src, encoding="utf-8"))
        json.dump(row, open(os.path.join(stage_root, instance_id + ".json"), "w"))
        staged.append(os.path.join(stage_root, instance_id + ".json"))
        shutil.copy(graph_pkl, os.path.join(stage_graph, instance_id + ".pkl"))
        staged.append(os.path.join(stage_graph, instance_id + ".pkl"))
        tags = json.load(open(tags_src, encoding="utf-8"))
        json.dump(tags, open(os.path.join(stage_graph, f"tags_{instance_id}.json"), "w"))
        staged.append(os.path.join(stage_graph, f"tags_{instance_id}.json"))
        env = dict(os.environ)
        env["PYTHONPATH"] = "." + os.pathsep + "agentless/"
        cwd = os.path.abspath("third_party/RepoGraph")
        loc_out = work + "/location"
        os.makedirs(loc_out, exist_ok=True)
        cmd = [sys.executable, "agentless/fl/localize.py", "--file_level",
               "--related_level", "--fine_grain_line_level",
               "--output_folder", loc_out, "--top_n", "3", "--compress",
               "--context_window", "10", "--repo_graph", "--target_id", instance_id,
               "--num_samples", "1", "--num_threads", "1"]
        r = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=cwd, timeout=2400)
        if r.returncode != 0:
            raise RuntimeError("fork localize failed: " + r.stderr[-1500:])
        loc_file = os.path.join(loc_out, "loc_outputs_codegraph.jsonl")
        if not os.path.exists(loc_file):
            loc_file = os.path.join(loc_out, "loc_outputs.jsonl")
        rows = [json.loads(l) for l in open(loc_file, encoding="utf-8").splitlines()]
        row = next(x for x in rows if x["instance_id"] == instance_id)
        found = row.get("found_files") or []
        rec["localization_top1"] = found[0] if found else None
        rec["localization_topk"] = found
        rdir = work + "/repair"
        os.makedirs(rdir, exist_ok=True)
        proc = run_agentless_repair(None, loc_file, rdir, instance_id, fork=True)
        patch_text, files, err = assemble_patch_text(proc)
        patch = Patch(hypothesis_id="", files_changed=files, patch_text=patch_text,
                      valid=bool(patch_text.strip()), error=err)
        rec["patch_attempts"] = 1
        rec["patch_valid"] = patch.valid
        if not patch.valid:
            rec["status"] = "UNRESOLVED"
            rec["failure_stage"] = "PATCH_SYNTAX"
        else:
            preds = sw.write_predictions(instance_id, patch_text, "repograph-phased-B",
                                         work + "/predictions.json")
            ev = sw.evaluate(preds, instance_id, f"phased-B-{instance_id}",
                             timeout=HARNESS_TIMEOUT)
            rec["FAIL_TO_PASS"] = {"passed": ev.fail_to_pass_passed, "total": ev.fail_to_pass_total}
            rec["PASS_TO_PASS"] = {"passed": ev.pass_to_pass_passed, "total": ev.pass_to_pass_total}
            cands = [Candidate(file=f) for f in found]
            fc = classify(patch, ev, cands)
            rec["status"] = "RESOLVED" if ev.resolved else (
                "TIMEOUT" if fc.value == "TIMEOUT" else ("INFRA_FAILURE" if fc.value == "INFRA_FAILURE" else "UNRESOLVED"))
            rec["failure_stage"] = None if ev.resolved else fc.value
            err_lower = (ev.error or "").lower()
            if not ev.resolved and ("patch apply failed" in err_lower or "malformed patch" in err_lower):
                rec["status"] = "UNRESOLVED"
                rec["failure_stage"] = "PATCH_SYNTAX"
        pin, pout = walk_usage(work)
        rec["input_tokens"], rec["output_tokens"] = pin, pout
        rec["total_tokens"] = pin + pout
        rec["cost_usd"] = round(estimate_cost(pin, pout, "B"), 6)
    except Exception as e:
        rec["status"] = "INFRA_FAILURE"
        rec["failure_stage"] = "INFRA_FAILURE"
        open(work + "/error.log", "w").write(traceback.format_exc()[-3000:])
        rec["error"] = str(e)[:300]
    finally:
        for p in staged:
            try:
                os.remove(p)
            except OSError:
                pass
    rec["runtime_s"] = round(time.time() - t0, 1)
    return rec


def run_arm_c(instance_id):
    t0 = time.time()
    from patchforge.core.orchestrator import Orchestrator
    cfg = PatchForgeConfig(default_model="openai/gpt-4o-mini",
                           max_hypotheses=3, max_patch_attempts=3,
                           max_task_seconds=1500, workspace=".")
    fresh_work(os.path.abspath(f"experiments/phase_c/work/{instance_id}"),
               os.path.abspath(f"experiments/phase_c/{instance_id}"))
    try:
        ep = Orchestrator(cfg, workspace=".").run_instance(instance_id)
        d = ep.to_dict()
        cands = d["localization"]["top_candidates"]
        rec = base_record(instance_id, d["base_commit"])
        rec["status"] = d["status"]
        rec["failure_stage"] = d["failure_stage"]
        rec["localization_top1"] = (cands[0]["file"] + "::" + cands[0]["symbol"]) if cands else None
        rec["localization_topk"] = [c["file"] + ("::" + c["symbol"] if c["symbol"] else "") for c in cands]
        rec["hypotheses_generated"] = len(d["hypotheses"])
        rec["selected_hypothesis"] = d["selected_hypothesis"]
        rec["patch_attempts"] = len(d["attempts"])
        rec["patch_valid"] = bool((d["patch"] or {}).get("valid", False))
        f2p = (d["tests"] or {}).get("fail_to_pass") or {}
        p2p = (d["tests"] or {}).get("pass_to_pass") or {}
        rec["FAIL_TO_PASS"] = {"passed": f2p.get("passed", 0), "total": f2p.get("total", 0)}
        rec["PASS_TO_PASS"] = {"passed": p2p.get("passed", 0), "total": p2p.get("total", 0)}
        rec["input_tokens"] = d["tokens"]["input"]
        rec["output_tokens"] = d["tokens"]["output"]
        rec["total_tokens"] = d["tokens"]["input"] + d["tokens"]["output"]
        rec["cost_usd"] = d["cost_usd"]
        rec["cost_estimated"] = False
        rec["runtime_s"] = d["runtime_seconds"]
        return rec
    except Exception as e:
        rec = base_record(instance_id, "")
        rec["status"] = "INFRA_FAILURE"
        rec["failure_stage"] = "INFRA_FAILURE"
        rec["error"] = str(e)[:300]
        rec["runtime_s"] = round(time.time() - t0, 1)
        return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", required=True, choices=ARMS)
    ap.add_argument("--instance", required=True)
    args = ap.parse_args()
    if load_record(args.arm, args.instance):
        print(f"SKIP {args.arm} {args.instance} (record exists)")
        return
    print(f"RUN {args.arm} {args.instance}", flush=True)
    rec = {"A": run_arm_a, "B": run_arm_b, "C": run_arm_c}[args.arm](args.instance)
    rec["arm"] = args.arm
    save_record(args.arm, args.instance, rec)
    print(f"DONE {args.arm} {args.instance}: {rec['status']} "
          f"F2P={rec['FAIL_TO_PASS']} cost=${rec['cost_usd']} t={rec['runtime_s']}s", flush=True)


if __name__ == "__main__":
    main()
