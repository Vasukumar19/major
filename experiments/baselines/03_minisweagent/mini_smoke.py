"""mini-SWE-agent smoke test: model + env + agent loop via OpenRouter.

Proves the execution layer works end-to-end (Phase B). Read-only task:
inspect the repo and report a fact. Uses the cheapest Phase-1 model.
"""
import json
import os
import shlex
import subprocess

assert os.getenv("OPENROUTER_API_KEY"), "Set OPENROUTER_API_KEY in the environment."

from minisweagent.agents.default import DefaultAgent
from minisweagent.environments.local import LocalEnvironment
from minisweagent.models.openrouter_model import OpenRouterModel
import minisweagent.environments.local as local_mod


def _to_wsl(path):
    p = path.replace("\\", "/")
    if len(p) >= 2 and p[1] == ":":
        return "/mnt/" + p[0].lower() + p[2:]
    return p


def _wsl_run(command, cwd, env, timeout):
    """Adapter shim (experiment script only, upstream untouched): run the
    agent's bash commands through WSL instead of CMD."""
    inner = "cd " + shlex.quote(_to_wsl(cwd)) + " && " + command
    process = subprocess.Popen(
        ["bash.exe", "-c", inner],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        encoding="utf-8",
        errors="replace",
    )
    try:
        stdout, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, _ = process.communicate()
        raise subprocess.TimeoutExpired(command, timeout, output=stdout)
    return subprocess.CompletedProcess(command, process.returncode, stdout=stdout)


local_mod._run = _wsl_run

REPO = r"C:\Users\kumar\project\major project\experiments\repos\requests-mini-smoke"

model = OpenRouterModel(
    model_name="openai/gpt-4o-mini",
    model_kwargs={"max_tokens": 2048},
    cost_tracking="ignore_errors",
)
env = LocalEnvironment(cwd=REPO)
agent = DefaultAgent(
    model,
    env,
    system_template=(
        "You are a helpful coding assistant. Use bash commands to inspect the repo. Answer concisely. "
        "When you have the answer, submit it by running a bash command whose first output line is "
        "exactly COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT followed by your answer on the next lines, e.g. "
        "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT && echo '463: def register_hook...'"
    ),
    instance_template="{{task}}",
    step_limit=8,
)

task = (
    "In this repository, open requests/models.py, find the register_hook method, "
    "and reply with exactly one line: the method's line number and its first line of code."
)
result = agent.run(task)
print("EXIT:", result.get("exit_status"))
print("SUBMISSION:", str(result.get("submission"))[:300])
print("N_MESSAGES:", len(agent.messages))
for msg in agent.messages[-3:]:
    print("---", msg.get("role"), "---")
    print(str(msg.get("content"))[:500])
json.dump(
    {
        "exit_status": result.get("exit_status"),
        "submission": str(result.get("submission"))[:300],
        "n_messages": len(agent.messages),
        "model": "openai/gpt-4o-mini",
    },
    open(r"C:\Users\kumar\project\major project\experiments\baselines\03_minisweagent\smoke.json", "w"),
)
