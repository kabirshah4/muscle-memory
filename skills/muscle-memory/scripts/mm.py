#!/usr/bin/env python3
"""muscle-memory: count repeated tasks, store compiled reflexes, recall them cheaply.

Store: ~/.claude/muscle-memory/
  index.json          {key: {count, triggers, summary, reflex, last_used, uses, fails}}
  reflexes/<key>.sh   deterministic reflex (run with args)
  reflexes/<key>.md   judgment reflex (terse recipe)

Commands:
  match "<task text>"                 -> best reflex for this task, or nothing
  log <key> "<summary>" [--triggers "a,b,c"]   -> record one manual run, print count + whether to compile
  save <key> <path-to-script-or-md>   -> install a reflex for <key>
  run <key> [args]                    -> execute a reflex, auto-record hit/fail (prints .md recipes)
  hit <key> | fail <key>              -> record reflex success / failure
  promote <key> | demote <key>        -> manually turn instinct (hook runs it with no model) on / off
  list                                -> all tasks with counts and reflex status
  show <key>                          -> print the reflex body
  forget <key>                        -> delete a task and its reflex
"""
import json, os, re, shutil, sys, time

HOME = os.path.expanduser(os.environ.get("MUSCLE_MEMORY_DIR", "~/.claude/muscle-memory"))
INDEX = os.path.join(HOME, "index.json")
REFLEXES = os.path.join(HOME, "reflexes")
THRESHOLD = int(os.environ.get("MUSCLE_MEMORY_THRESHOLD", "3"))
INSTINCT_AFTER = int(os.environ.get("MUSCLE_MEMORY_INSTINCT_AFTER", "3"))  # clean no-arg runs before the hook runs it alone
INSTINCT_SCORE = 0.75  # stricter match required to run without the model
BYPASS = "no reflex"   # put this phrase in a prompt to skip reflexes
STOP = set(("a an the to of for and or in on at with my me i it this that please can you do make run "
            "give show get again same usual last time now just want need let lets us could would will hey ok").split())


def load():
    try:
        with open(INDEX) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def store(idx):
    os.makedirs(REFLEXES, exist_ok=True)
    tmp = INDEX + ".tmp"
    with open(tmp, "w") as f:
        json.dump(idx, f, indent=2)
    os.replace(tmp, INDEX)


def toks(text):
    return {t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in STOP and len(t) > 1}


def slug(key):
    s = re.sub(r"[^a-z0-9-]+", "-", key.lower()).strip("-")
    if not s:
        sys.exit("error: key must contain letters or digits")
    return s


def score(task_toks, entry, key):
    vocab = toks(" ".join(entry.get("triggers", [])) + " " + key.replace("-", " ") + " " + entry.get("summary", ""))
    if not vocab or not task_toks:
        return 0.0
    hit = len(task_toks & vocab)
    return hit / min(len(task_toks), len(vocab))


def cmd_match(text, quiet=False):
    idx, t = load(), toks(text)
    best = max(((score(t, e, k), k) for k, e in idx.items() if e.get("reflex")), default=(0, None))
    if best[0] < 0.5:
        return None
    sc = best[0]
    k = best[1]
    e = idx[k]
    if not quiet:
        print(f"REFLEX {k} ({e['reflex']}) score={best[0]:.2f} uses={e.get('uses',0)} fails={e.get('fails',0)}")
        print(f"summary: {e.get('summary','')}")
        print(f"path: {os.path.join(REFLEXES, e['reflex'])}")
    return k, e, sc


def cmd_log(key, summary, triggers):
    key, idx = slug(key), load()
    e = idx.setdefault(key, {"count": 0, "triggers": [], "summary": "", "reflex": None, "uses": 0, "fails": 0})
    e["count"] += 1
    e["summary"] = summary or e["summary"]
    e["triggers"] = sorted(set(e["triggers"]) | {x.strip().lower() for x in triggers if x.strip()})
    e["last_used"] = time.strftime("%Y-%m-%d")
    store(idx)
    print(f"{key}: seen {e['count']}x")
    if e["reflex"]:
        print("reflex already exists - you should have used it (run: mm.py show " + key + ")")
    elif e["count"] >= THRESHOLD:
        print(f"COMPILE NOW: threshold {THRESHOLD} reached. Write the reflex and run: mm.py save {key} <file>")
    else:
        print(f"{THRESHOLD - e['count']} more run(s) before compiling")


def cmd_save(key, path):
    key, idx = slug(key), load()
    ext = os.path.splitext(path)[1]
    if ext not in (".sh", ".py", ".md"):
        sys.exit("error: reflex must be .sh, .py or .md")
    os.makedirs(REFLEXES, exist_ok=True)
    for old in os.listdir(REFLEXES):
        if os.path.splitext(old)[0] == key:
            os.remove(os.path.join(REFLEXES, old))
    dest = os.path.join(REFLEXES, key + ext)
    shutil.copyfile(path, dest)
    if ext != ".md":
        os.chmod(dest, 0o755)
    e = idx.setdefault(key, {"count": 0, "triggers": [], "summary": "", "uses": 0, "fails": 0})
    e["reflex"], e["fails"] = key + ext, 0
    store(idx)
    print(f"saved reflex {dest}")


def bump(key, field, with_args=None, quiet=False):
    """Record a reflex outcome. with_args: None = unknown (manual hit/fail), True/False = how it was run."""
    out = (lambda *a: None) if quiet else print
    key, idx = slug(key), load()
    if key not in idx:
        sys.exit(f"unknown task {key}")
    e = idx[key]
    e[field] = e.get(field, 0) + 1
    e["last_used"] = time.strftime("%Y-%m-%d")
    if field == "uses":
        e["fails"] = 0
        if with_args is False and e.get("reflex", "").endswith((".sh", ".py")):
            e["streak"] = e.get("streak", 0) + 1
        elif with_args:
            e["streak"] = 0
        if e.get("streak", 0) >= INSTINCT_AFTER and not e.get("instinct"):
            e["instinct"] = True
            out(f"{key} promoted to INSTINCT: the prompt hook will now run it directly, with no model call")
    else:
        e["streak"], e["instinct"] = 0, False
        if e["fails"] >= 2 and e.get("reflex"):
            out(f"{key} failed twice in a row - reflex disabled, fix and re-save it")
            os.rename(os.path.join(REFLEXES, e["reflex"]), os.path.join(REFLEXES, e["reflex"] + ".broken"))
            e["reflex"] = None
    store(idx)
    out(f"{key}: uses={e.get('uses',0)} fails={e.get('fails',0)}")


def cmd_list():
    idx = load()
    if not idx:
        print("no tasks recorded yet")
    for k, e in sorted(idx.items(), key=lambda kv: -kv[1]["count"]):
        if e.get("instinct"):
            status = f"INSTINCT {e['reflex']} uses={e.get('uses',0)}"
        elif e.get("reflex"):
            status = f"reflex {e['reflex']} uses={e.get('uses',0)} instinct-in={max(INSTINCT_AFTER - e.get('streak',0),0)}"
        else:
            status = f"learning ({e['count']}/{THRESHOLD})"
        print(f"{k:30} seen={e['count']:<3} {status}  - {e.get('summary','')}")


SELF = os.path.abspath(__file__)
MIN_CALLS = int(os.environ.get("MUSCLE_MEMORY_MIN_CALLS", "3"))


def turn_tool_calls(transcript_path):
    """Tool calls made since the last real user prompt in a Claude Code transcript."""
    calls = []
    with open(transcript_path) as f:
        for line in f:
            try:
                m = json.loads(line)
            except json.JSONDecodeError:
                continue
            content = (m.get("message") or {}).get("content")
            if m.get("type") == "user" and not m.get("isMeta"):
                is_tool_result = isinstance(content, list) and any(
                    isinstance(b, dict) and b.get("type") == "tool_result" for b in content)
                if not is_tool_result:
                    calls = []  # a new user prompt starts a new turn
            elif m.get("type") == "assistant" and isinstance(content, list):
                calls += [b for b in content if isinstance(b, dict) and b.get("type") == "tool_use"]
    return calls


def cmd_stop_hook():
    # Stop hook: after a non-trivial turn that wasn't logged, ask Claude to log it (once).
    try:
        data = json.load(sys.stdin)
        if data.get("stop_hook_active"):
            return
        calls = turn_tool_calls(data["transcript_path"])
    except Exception:
        return
    mm_cmds = [json.dumps(c.get("input", {})) for c in calls if "mm.py" in json.dumps(c.get("input", {}))]
    if any(re.search(r"mm\.py\W+(log|hit|fail|save|run)\b", s) for s in mm_cmds):
        return  # already logged or used a reflex this turn
    work = [c for c in calls if "mm.py" not in json.dumps(c.get("input", {}))]
    if len(work) < MIN_CALLS:
        return
    keys = ", ".join(sorted(load())) or "none yet"
    reason = (f"[muscle-memory] That task took {len(work)} tool calls. If it's the kind of task that could recur, "
              f"log it: `python3 {SELF} log <task-key> \"<one-line summary>\" --triggers \"word,word\"`. "
              f"Reuse an existing key if one fits (existing: {keys}). If the output says COMPILE NOW, write the "
              f"reflex as the muscle-memory skill describes and `save` it. If it's a one-off, just stop.")
    print(json.dumps({"decision": "block", "reason": reason}))


def cmd_run(key, args):
    """Execute a script reflex (records hit/fail from exit code) or print a recipe reflex."""
    import subprocess
    k = slug(key)
    e = load().get(k)
    if not e or not e.get("reflex"):
        sys.exit(f"no reflex for {k}")
    path = os.path.join(REFLEXES, e["reflex"])
    if path.endswith(".md"):
        print(open(path).read())
        print(f"(recipe reflex: follow it, then run `python3 {SELF} hit {k}` or `fail {k}`)")
        return
    interp = ["python3"] if path.endswith(".py") else ["bash"]
    rc = subprocess.call(interp + [path] + args)
    bump(k, "uses" if rc == 0 else "fails", with_args=bool(args))
    sys.exit(rc)


def cmd_prompt_hook():
    """UserPromptSubmit hook. Instinct: run the reflex here and answer with no model call.
    Reflex: inject a one-line hint so Claude runs it instead of re-deriving the steps."""
    import subprocess
    try:
        data = json.load(sys.stdin)
        prompt = data.get("prompt", "")
    except Exception:
        return
    if BYPASS in prompt.lower():
        return
    m = cmd_match(prompt, quiet=True)
    if not m:
        return
    k, e, sc = m
    path = os.path.join(REFLEXES, e["reflex"])
    if e.get("instinct") and sc >= INSTINCT_SCORE and path.endswith((".sh", ".py")):
        interp = "python3" if path.endswith(".py") else "bash"
        try:
            r = subprocess.run([interp, path], cwd=data.get("cwd") or None, capture_output=True, text=True, timeout=110)
            rc, output = r.returncode, (r.stdout + r.stderr).strip()
        except subprocess.TimeoutExpired:
            rc, output = 124, "timed out"
        if len(output) > 4000:
            output = "...\n" + output[-4000:]
        if rc == 0:
            bump(k, "uses", with_args=False, quiet=True)
            print(json.dumps({"decision": "block", "reason":
                f"⚡ muscle-memory: ran '{k}' by instinct (no model call). Add \"{BYPASS}\" to your prompt to have Claude do it instead.\n\n{output}"}))
            return
        bump(k, "fails", quiet=True)
        print(f"[muscle-memory] The instinct '{k}' just failed (exit {rc}) and was demoted. Output:\n{output}\n"
              f"Do the task normally, then fix the reflex and re-save it with `python3 {SELF} save {k} <file>`.")
        return
    print(f"[muscle-memory] Reflex '{k}' matches this task ({e.get('summary','')}). "
          f"Run it instead of re-deriving the steps: `python3 {SELF} run {k} [args]` (it executes the reflex and records "
          f"the outcome; no need to read the script first). Only if it fails, fall back to doing the task normally.")


def main(a):
    if not a:
        sys.exit(__doc__)
    c = a[0]
    if c == "match":
        if not cmd_match(" ".join(a[1:])):
            print("no reflex")
    elif c == "log":
        trig = []
        if "--triggers" in a:
            i = a.index("--triggers")
            trig = a[i + 1].split(",")
            a = a[:i] + a[i + 2:]
        cmd_log(a[1], " ".join(a[2:]), trig)
    elif c == "save":
        cmd_save(a[1], a[2])
    elif c == "hit":
        bump(a[1], "uses")
    elif c == "fail":
        bump(a[1], "fails")
    elif c == "list":
        cmd_list()
    elif c == "show":
        e = load().get(slug(a[1]))
        if not e or not e.get("reflex"):
            sys.exit("no reflex for " + a[1])
        print(open(os.path.join(REFLEXES, e["reflex"])).read())
    elif c == "forget":
        idx, k = load(), slug(a[1])
        e = idx.pop(k, None)
        if e and e.get("reflex"):
            os.remove(os.path.join(REFLEXES, e["reflex"]))
        store(idx)
        print("forgot " + k)
    elif c == "hook":
        cmd_prompt_hook()
    elif c in ("promote", "demote"):
        idx, k = load(), slug(a[1])
        if k not in idx or not idx[k].get("reflex"):
            sys.exit("no reflex for " + k)
        idx[k]["instinct"] = c == "promote"
        store(idx)
        print(f"{k}: instinct={'on' if c == 'promote' else 'off'}")
    elif c == "run":
        cmd_run(a[1], a[2:])
    elif c == "stop-hook":
        cmd_stop_hook()
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
