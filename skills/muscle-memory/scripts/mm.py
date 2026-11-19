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
  hit <key> | fail <key>              -> record reflex success / failure
  list                                -> all tasks with counts and reflex status
  show <key>                          -> print the reflex body
  forget <key>                        -> delete a task and its reflex
"""
import json, os, re, shutil, sys, time

HOME = os.path.expanduser(os.environ.get("MUSCLE_MEMORY_DIR", "~/.claude/muscle-memory"))
INDEX = os.path.join(HOME, "index.json")
REFLEXES = os.path.join(HOME, "reflexes")
THRESHOLD = int(os.environ.get("MUSCLE_MEMORY_THRESHOLD", "3"))
STOP = set("a an the to of for and or in on at with my me i it this that please can you do make run".split())


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
    k = best[1]
    e = idx[k]
    if not quiet:
        print(f"REFLEX {k} ({e['reflex']}) score={best[0]:.2f} uses={e.get('uses',0)} fails={e.get('fails',0)}")
        print(f"summary: {e.get('summary','')}")
        print(f"path: {os.path.join(REFLEXES, e['reflex'])}")
    return k, e


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


def bump(key, field):
    key, idx = slug(key), load()
    if key not in idx:
        sys.exit(f"unknown task {key}")
    idx[key][field] = idx[key].get(field, 0) + 1
    idx[key]["last_used"] = time.strftime("%Y-%m-%d")
    if field == "fails" and idx[key]["fails"] >= 2 and idx[key].get("reflex"):
        print(f"{key} failed twice in a row - reflex disabled, fix and re-save it")
        os.rename(os.path.join(REFLEXES, idx[key]["reflex"]), os.path.join(REFLEXES, idx[key]["reflex"] + ".broken"))
        idx[key]["reflex"] = None
    if field == "uses":
        idx[key]["fails"] = 0
    store(idx)
    print(f"{key}: uses={idx[key].get('uses',0)} fails={idx[key].get('fails',0)}")


def cmd_list():
    idx = load()
    if not idx:
        print("no tasks recorded yet")
    for k, e in sorted(idx.items(), key=lambda kv: -kv[1]["count"]):
        status = f"reflex={e['reflex']} uses={e.get('uses',0)}" if e.get("reflex") else f"learning ({e['count']}/{THRESHOLD})"
        print(f"{k:30} seen={e['count']:<3} {status}  - {e.get('summary','')}")


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
        # UserPromptSubmit hook: read prompt JSON on stdin, inject a one-line hint if a reflex matches.
        try:
            prompt = json.load(sys.stdin).get("prompt", "")
        except Exception:
            return
        m = cmd_match(prompt, quiet=True)
        if m:
            k, e = m
            print(f"[muscle-memory] Reflex '{k}' matches this task ({e.get('summary','')}). "
                  f"Use the muscle-memory skill: run `mm.py show {k}` and execute it instead of re-deriving the steps.")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
