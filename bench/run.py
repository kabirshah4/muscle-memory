#!/usr/bin/env python3
"""Reproducible muscle-memory benchmark.

For each task type it builds a fixture git repo, then drives real headless Claude Code
sessions (`claude -p`) through the lifecycle and records cost, tokens, time and whether
the output was correct:

  hand x3      plugin on, empty store: Claude does it by hand (and logs/compiles it)
  reflex x3    plugin on: Claude runs the compiled reflex
  instinct x1  plugin on, after `mm promote --confirm` (only if the reflex became eligible)
  control x2   plugin off, fresh store: the same prompt as reflex/instinct, for comparison

Usage:
  python3 bench/run.py                       # all tasks, writes bench/results.csv
  python3 bench/run.py --tasks changelog     # one task
  python3 bench/run.py --model claude-sonnet-5-5

Requires an authenticated `claude` CLI. Costs real money: roughly $0.5-1.5 per task.
The installed copy of the plugin (if any) is disabled for every run via --settings, and
this checkout is loaded with --plugin-dir, so runs never double-count.
"""
import argparse
import csv
import json
import os
import platform
import pty
import re
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MM = os.path.join(REPO, "skills", "muscle-memory", "scripts", "mm.py")
sys.path.insert(0, HERE)
from tasks import TASKS  # noqa: E402

class BenchAborted(RuntimeError):
    """A session failed for reasons unrelated to the plugin; its numbers would be meaningless."""


DISABLE_INSTALLED = json.dumps({"enabledPlugins": {"muscle-memory@muscle-memory": False}})
FIELDS = ["task", "run", "phase", "used_reflex", "state_before", "ok", "cost_usd", "output_tokens", "cache_read_tokens",
          "cache_write_tokens", "input_tokens", "tool_calls", "duration_s", "note"]


def git(cwd, *args):
    subprocess.run(["git", "-C", cwd, *args], check=True, capture_output=True)


def make_fixture(task, root, name):
    d = os.path.join(root, name)
    os.makedirs(d)
    git(d, "init", "-q", "-b", "main")
    git(d, "config", "user.email", "bench@example.com")
    git(d, "config", "user.name", "bench")
    task["setup"](d, git)
    return d


def reset(d):
    git(d, "checkout", "-q", "--", ".")
    git(d, "clean", "-fdqx")


def condense(stdout):
    """Keep what a reader needs to audit a run: tool calls (truncated), hook context and the result."""
    keep = []
    for line in stdout.splitlines():
        try:
            m = json.loads(line)
        except json.JSONDecodeError:
            continue
        if m.get("type") == "assistant":
            for c in m["message"]["content"]:
                if c.get("type") == "tool_use":
                    keep.append({"tool": c["name"], "input": json.dumps(c["input"])[:400]})
                elif c.get("type") == "text":
                    keep.append({"text": c["text"][:400]})
        elif m.get("type") == "result":
            keep.append({k: m.get(k) for k in ("subtype", "total_cost_usd", "num_turns", "duration_ms", "usage", "result")})
    return keep


def claude(cwd, prompt, store, plugin, model, raw_path):
    cmd = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose",
           "--settings", DISABLE_INSTALLED,
           "--allowedTools", "Bash Read Write Edit Glob Grep Skill"]
    if plugin:
        cmd += ["--plugin-dir", REPO]
    if model:
        cmd += ["--model", model]
    env = dict(os.environ, MUSCLE_MEMORY_DIR=store)
    t0 = time.time()
    p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, timeout=900)
    dur = time.time() - t0
    os.makedirs(os.path.dirname(raw_path), exist_ok=True)
    with open(raw_path, "w") as f:
        json.dump({"prompt": prompt, "plugin": plugin, "events": condense(p.stdout)}, f, indent=1)
    calls, result, used = 0, {}, False
    for line in p.stdout.splitlines():
        try:
            m = json.loads(line)
        except json.JSONDecodeError:
            continue
        if m.get("type") == "assistant":
            for c in m["message"]["content"]:
                if c.get("type") == "tool_use":
                    calls += 1
                    if re.search(r"(mm\.py|\$\{?MM\}?)\W+run\b", json.dumps(c.get("input", {}))):
                        used = True
        elif m.get("type") == "result":
            result = m
    text = str(result.get("result", ""))
    if result.get("is_error") or "hit your" in text and "limit" in text or (result and not calls
            and "by muscle-memory, with no model call" not in text and not (result.get("usage") or {}).get("output_tokens")):
        raise BenchAborted(f"session did not run (usage limit or API error): {text[:160]!r}")
    u = result.get("usage") or {}
    return {
        "cost_usd": round(result.get("total_cost_usd") or 0, 4),
        "output_tokens": u.get("output_tokens", 0),
        "cache_read_tokens": u.get("cache_read_input_tokens", 0),
        "cache_write_tokens": u.get("cache_creation_input_tokens", 0),
        "input_tokens": u.get("input_tokens", 0),
        "tool_calls": calls,
        "used_reflex": used or "by muscle-memory, with no model call" in str(result.get("result", "")),
        "duration_s": round(dur, 1),
        "note": "" if result else f"no result (exit {p.returncode}): {p.stderr[-200:]}",
    }


def mm(store, cwd, *args):
    env = dict(os.environ, MUSCLE_MEMORY_DIR=store)
    return subprocess.run([sys.executable, MM, *args], cwd=cwd, env=env,
                          capture_output=True, text=True).stdout


def promote_tty(store, cwd, key):
    """Stand-in for the user: run `promote` on a pseudo-terminal and type 'yes'."""
    pid, fd = pty.fork()
    if pid == 0:
        os.chdir(cwd)
        os.environ["MUSCLE_MEMORY_DIR"] = store
        os.execv(sys.executable, [sys.executable, MM, "promote", key])
    out, sent = b"", False
    while True:
        try:
            chunk = os.read(fd, 4096)
        except OSError:
            break
        if not chunk:
            break
        out += chunk
        if not sent and b"Type 'yes'" in out:
            os.write(fd, b"yes\n")
            sent = True
    os.waitpid(pid, 0)
    os.close(fd)
    return out.decode(errors="replace")


def approve_all(store, cwd):
    """Approve every eligible reflex, as the user would after reviewing it."""
    approved = []
    for line in mm(store, cwd, "list").splitlines():
        if "ELIGIBLE" in line:
            key = line.split()[0]
            if "instinct ON" in promote_tty(store, cwd, key):
                approved.append(key)
    return approved


def run_task(name, model, root, raw_dir):
    task = TASKS[name]
    rows = []
    store = os.path.join(root, name + "-store")
    d = make_fixture(task, root, name)

    def record(run, phase, prompt, plugin, st, cwd):
        reset(cwd)
        if task.get("reset"):
            task["reset"](cwd, git)
        state = " / ".join(l.strip() for l in mm(st, cwd, "list").splitlines() if not l.startswith("["))[:200]
        r = claude(cwd, prompt, st, plugin, model, os.path.join(raw_dir, name, f"run{run}-{phase}.json"))
        r.update(task=name, run=run, phase=phase, ok=task["check"](cwd, run), state_before=state)
        rows.append(r)
        print(f"[{name}] run {run:<2} {phase:<8} ok={r['ok']!s:<5} used_reflex={r['used_reflex']!s:<5} ${r['cost_usd']:.3f} "
              f"calls={r['tool_calls']} {r['duration_s']}s {r['note']}", flush=True)

    for i, prompt in enumerate(task["hand_prompts"], 1):
        record(i, "hand", prompt, True, store, d)
    for i in range(4, 7):
        record(i, "reflex", task["repeat_prompt"](i), True, store, d)
    if approve_all(store, d):
        record(7, "instinct", task["repeat_prompt"](7), True, store, d)
    else:
        listing = mm(store, d, "list").strip().replace("\n", " / ")
        rows.append(dict(task=name, run=7, phase="instinct", ok="", note=f"not eligible: {listing}"[:300]))
        print(f"[{name}] run 7  instinct skipped: not eligible", flush=True)
    c = make_fixture(task, root, name + "-control")
    for i in (8, 9):
        record(i, "control", task["repeat_prompt"](i), False, os.path.join(root, name + "-cstore"), c)
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tasks", default=",".join(TASKS), help="comma-separated: " + ", ".join(TASKS))
    ap.add_argument("--out", default=os.path.join(HERE, "results.csv"))
    ap.add_argument("--model", default=None, help="model for every session (default: CLI default)")
    ap.add_argument("--keep", action="store_true", help="keep the fixture/store temp dir")
    a = ap.parse_args()
    if not shutil.which("claude"):
        sys.exit("error: the `claude` CLI is not on PATH")
    names = [t for t in a.tasks.split(",") if t]
    unknown = [t for t in names if t not in TASKS]
    if unknown:
        sys.exit(f"unknown task(s): {', '.join(unknown)}")
    root = tempfile.mkdtemp(prefix="mm-bench-")
    raw_dir = os.path.join(root, "raw")  # copied into bench/raw only if the whole run succeeds
    version = subprocess.run(["claude", "--version"], capture_output=True, text=True).stdout.strip()
    commit = subprocess.run(["git", "-C", REPO, "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    try:
        try:
            with ThreadPoolExecutor(len(names)) as ex:
                results = list(ex.map(lambda n: run_task(n, a.model, root, raw_dir), names))
        except BenchAborted as err:
            sys.exit(f"benchmark aborted, results.csv left unchanged: {err}")
        shutil.rmtree(os.path.join(HERE, "raw"), ignore_errors=True)
        shutil.copytree(raw_dir, os.path.join(HERE, "raw"))
    finally:
        if a.keep:
            print("kept", root)
        else:
            shutil.rmtree(root, ignore_errors=True)
    with open(a.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for rows in results:
            for r in rows:
                w.writerow({k: r.get(k, "") for k in FIELDS})
    with open(os.path.splitext(a.out)[0] + ".meta.json", "w") as f:
        json.dump({"date": time.strftime("%Y-%m-%d"), "claude_cli": version, "model": a.model or "CLI default",
                   "plugin_commit": commit, "platform": platform.platform(), "tasks": names}, f, indent=2)
    print("wrote", a.out)


if __name__ == "__main__":
    main()
