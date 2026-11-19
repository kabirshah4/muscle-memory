#!/usr/bin/env python3
"""muscle-memory: count repeated tasks, store compiled reflexes, recall them cheaply.

Reflexes are scoped to the project (git root, else the working directory) they were
learned in. Pass --global to log/save to make one available everywhere.

Store: ~/.claude/muscle-memory/
  projects/<name>-<hash>/index.json, reflexes/   per-project
  global/index.json, reflexes/                   everywhere

Commands:
  match "<task text>"                       best reflex for this task here, or nothing
  log <key> "<summary>" [--triggers a,b] [--global]   record one manual run
  save <key> <file> [--global]              install a reflex (.sh, .py or .md)
  run <key> [args...]                       execute a reflex, auto-record hit/fail
  hit <key> | fail <key>                    record a reflex outcome by hand
  promote <key> [--confirm <hash>]          review, then approve, an instinct (hook runs it, no model)
  demote <key>                              turn an instinct off
  list [--all]                              tasks here + global (--all: every project)
  show <key> | forget <key>                 print / delete a reflex
  hook | stop-hook                          Claude Code hook entry points (read JSON on stdin)
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager

try:
    import fcntl
except ImportError:  # Windows: no locking (unsupported platform, but don't crash)
    fcntl = None

ROOT = os.path.expanduser(os.environ.get("MUSCLE_MEMORY_DIR", "~/.claude/muscle-memory"))
THRESHOLD = int(os.environ.get("MUSCLE_MEMORY_THRESHOLD", "3"))
INSTINCT_AFTER = int(os.environ.get("MUSCLE_MEMORY_INSTINCT_AFTER", "3"))
MIN_CALLS = int(os.environ.get("MUSCLE_MEMORY_MIN_CALLS", "3"))
ASK_NEW = os.environ.get("MUSCLE_MEMORY_ASK", "1") != "0"
MATCH_SCORE = 0.5
INSTINCT_SCORE = 0.75   # plus: >= 2 shared words and a short prompt (see strong_match)
INSTINCT_MAX_WORDS = 12
BYPASS = "no reflex"
SELF = os.path.abspath(__file__)
GLOBAL = "global"
STOP = set((
    "a an the to of for and or in on at with my me i it this that please can you do make run "
    "give show get again same usual last time now just want need let lets us could would will hey ok"
).split())


# ---------- scopes & storage ----------

def project_root(path=None):
    path = os.path.realpath(path or os.getcwd())
    try:
        r = subprocess.run(["git", "-C", path, "rev-parse", "--show-toplevel"],
                           capture_output=True, text=True, timeout=3)
        if r.returncode == 0 and r.stdout.strip():
            return os.path.realpath(r.stdout.strip())
    except Exception:
        pass
    return path


def scope_dir(scope):
    if scope == GLOBAL:
        return os.path.join(ROOT, GLOBAL)
    name = re.sub(r"[^a-zA-Z0-9]+", "-", os.path.basename(scope)).strip("-")[:40] or "root"
    return os.path.join(ROOT, "projects", f"{name}-{hashlib.sha1(scope.encode()).hexdigest()[:8]}")


def reflex_path(scope, e):
    return os.path.join(scope_dir(scope), "reflexes", e["reflex"])


def read(scope):
    try:
        with open(os.path.join(scope_dir(scope), "index.json")) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


@contextmanager
def lock():
    os.makedirs(ROOT, exist_ok=True)
    with open(os.path.join(ROOT, ".lock"), "w") as fh:
        if fcntl:
            fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl:
                fcntl.flock(fh, fcntl.LOCK_UN)


@contextmanager
def edit(scope):
    """Locked read-modify-write of one scope's index."""
    with lock():
        idx = read(scope)
        yield idx
        d = scope_dir(scope)
        os.makedirs(os.path.join(d, "reflexes"), exist_ok=True)
        with open(os.path.join(d, "scope.json"), "w") as f:
            json.dump({"root": scope}, f)
        tmp = os.path.join(d, "index.json.tmp")
        with open(tmp, "w") as f:
            json.dump(idx, f, indent=2)
        os.replace(tmp, os.path.join(d, "index.json"))


def migrate_legacy():
    """v0.1/v0.2 kept one flat store. Move it to global with instincts switched off."""
    old = os.path.join(ROOT, "index.json")
    if not os.path.exists(old):
        return
    with lock():
        if not os.path.exists(old):
            return
        gd = scope_dir(GLOBAL)
        os.makedirs(os.path.join(gd, "reflexes"), exist_ok=True)
        with open(old) as f:
            idx = json.load(f)
        for e in idx.values():
            e["instinct"], e["streak"] = False, 0
        legacy = os.path.join(ROOT, "reflexes")
        if os.path.isdir(legacy):
            for name in os.listdir(legacy):
                shutil.move(os.path.join(legacy, name), os.path.join(gd, "reflexes", name))
            os.rmdir(legacy)
        with open(os.path.join(gd, "index.json"), "w") as f:
            json.dump(idx, f, indent=2)
        os.remove(old)


# ---------- helpers ----------

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
    return len(task_toks & vocab) / min(len(task_toks), len(vocab))


def find(project, text, need_reflex=True):
    """Best (scope, key, entry, score) for text in this project or global; project wins ties."""
    t, best = toks(text), None
    for scope in (project, GLOBAL):
        for k, e in read(scope).items():
            if need_reflex and not e.get("reflex"):
                continue
            sc = score(t, e, k)
            if sc >= MATCH_SCORE and (best is None or sc > best[3]):
                best = (scope, k, e, sc)
    return best


def strong_match(text, entry, key):
    """Stricter test for running with no model: high score, >= 2 shared words, short prompt."""
    t = toks(text)
    vocab = toks(" ".join(entry.get("triggers", [])) + " " + key.replace("-", " ") + " " + entry.get("summary", ""))
    return score(t, entry, key) >= INSTINCT_SCORE and len(t & vocab) >= 2 and len(t) <= INSTINCT_MAX_WORDS


def file_hash(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def resolve(project, key):
    k = slug(key)
    for scope in (project, GLOBAL):
        e = read(scope).get(k)
        if e:
            return scope, k, e
    sys.exit(f"unknown task '{k}' in this project or global (see: mm.py list)")


def new_entry():
    return {"count": 0, "triggers": [], "summary": "", "reflex": None, "uses": 0, "fails": 0}


# ---------- commands ----------

def cmd_log(scope, key, summary, triggers):
    key = slug(key)
    with edit(scope) as idx:
        e = idx.setdefault(key, new_entry())
        e["count"] += 1
        e["summary"] = summary or e["summary"]
        e["triggers"] = sorted(set(e["triggers"]) | {x.strip().lower() for x in triggers if x.strip()})
        e["last_used"] = time.strftime("%Y-%m-%d")
    where = "global" if scope == GLOBAL else "this project"
    print(f"{key}: seen {e['count']}x ({where})")
    if e["reflex"]:
        print(f"reflex already exists - next time run it: python3 {SELF} run {key}")
    elif e["count"] >= THRESHOLD:
        print(f"COMPILE NOW: threshold {THRESHOLD} reached. Write the reflex and run: python3 {SELF} save {key} <file>"
              + (" --global" if scope == GLOBAL else ""))
    else:
        print(f"{THRESHOLD - e['count']} more run(s) before compiling")


def cmd_save(scope, key, path):
    key = slug(key)
    ext = os.path.splitext(path)[1]
    if ext not in (".sh", ".py", ".md"):
        sys.exit("error: reflex must be .sh, .py or .md")
    with edit(scope) as idx:
        rdir = os.path.join(scope_dir(scope), "reflexes")
        os.makedirs(rdir, exist_ok=True)
        for old in os.listdir(rdir):
            if old.split(".")[0] == key:
                os.remove(os.path.join(rdir, old))
        dest = os.path.join(rdir, key + ext)
        shutil.copyfile(path, dest)
        if ext != ".md":
            os.chmod(dest, 0o755)
        e = idx.setdefault(key, new_entry())
        e.update(reflex=key + ext, fails=0, streak=0, instinct=False, eligible=False, approved_sha=None)
    print(f"saved reflex {dest}")


def bump(scope, key, field, with_args=None, quiet=False):
    """Record a reflex outcome. with_args: None = unknown (manual hit/fail), True/False = how it ran."""
    out = (lambda *a: None) if quiet else print
    with edit(scope) as idx:
        e = idx[key]
        e[field] = e.get(field, 0) + 1
        e["last_used"] = time.strftime("%Y-%m-%d")
        if field == "uses":
            e["fails"] = 0
            if with_args is False and (e.get("reflex") or "").endswith((".sh", ".py")):
                e["streak"] = e.get("streak", 0) + 1
            elif with_args:
                e["streak"] = 0
            if e.get("streak", 0) >= INSTINCT_AFTER and not e.get("instinct") and not e.get("eligible"):
                e["eligible"] = True
                out(f"{key} is now ELIGIBLE to become an instinct (runs with no model call). It is NOT promoted. "
                    f"Tell the user they can review and approve it with: mm promote {key}")
        else:
            e["streak"], e["instinct"], e["eligible"] = 0, False, False
            if e["fails"] >= 2 and e.get("reflex"):
                out(f"{key} failed twice in a row - reflex disabled, fix and re-save it")
                p = reflex_path(scope, e)
                if os.path.exists(p):
                    os.rename(p, p + ".broken")
                e["reflex"] = None
    out(f"{key}: uses={e.get('uses', 0)} fails={e.get('fails', 0)}")


def cmd_run(project, key, args):
    scope, k, e = resolve(project, key)
    if not e.get("reflex"):
        sys.exit(f"'{k}' has no reflex yet")
    path = reflex_path(scope, e)
    if path.endswith(".md"):
        with open(path) as f:
            print(f.read())
        print(f"(recipe reflex: follow it, then run `python3 {SELF} hit {k}` or `fail {k}`)")
        return
    rc = subprocess.call(["python3" if path.endswith(".py") else "bash", path] + args)
    bump(scope, k, "uses" if rc == 0 else "fails", with_args=bool(args))
    sys.exit(rc)


def cmd_list(project, all_projects):
    scopes = [project, GLOBAL]
    if all_projects:
        pdir = os.path.join(ROOT, "projects")
        for d in sorted(os.listdir(pdir)) if os.path.isdir(pdir) else []:
            try:
                with open(os.path.join(pdir, d, "scope.json")) as f:
                    root = json.load(f)["root"]
            except Exception:
                continue
            if root != project:
                scopes.append(root)
    shown = False
    for scope in scopes:
        idx = read(scope)
        if not idx:
            continue
        shown = True
        print(f"[{'global' if scope == GLOBAL else scope}]")
        for k, e in sorted(idx.items(), key=lambda kv: -kv[1]["count"]):
            if e.get("instinct"):
                status = f"INSTINCT {e['reflex']} uses={e.get('uses', 0)}"
            elif e.get("eligible"):
                status = f"reflex {e['reflex']} uses={e.get('uses', 0)} ELIGIBLE (approve: mm promote {k})"
            elif e.get("reflex"):
                status = (f"reflex {e['reflex']} uses={e.get('uses', 0)} "
                          f"instinct-in={max(INSTINCT_AFTER - e.get('streak', 0), 0)}")
            else:
                status = f"learning ({e['count']}/{THRESHOLD})"
            print(f"  {k:28} seen={e['count']:<3} {status}  - {e.get('summary', '')}")
    if not shown:
        print("no tasks recorded yet")


# ---------- hooks ----------

def last_turn(transcript_path):
    """(prompt text, tool_use blocks) for the latest turn of a Claude Code transcript."""
    prompt, calls = "", []
    with open(transcript_path) as f:
        for line in f:
            try:
                m = json.loads(line)
            except json.JSONDecodeError:
                continue
            content = (m.get("message") or {}).get("content")
            if m.get("type") == "user" and not m.get("isMeta"):
                if isinstance(content, str):
                    prompt, calls = content, []
                elif isinstance(content, list) and not any(
                        isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
                    prompt = " ".join(b.get("text", "") for b in content if isinstance(b, dict))
                    calls = []
            elif m.get("type") == "assistant" and isinstance(content, list):
                calls += [b for b in content if isinstance(b, dict) and b.get("type") == "tool_use"]
    return prompt, calls


def cmd_prompt_hook():
    """UserPromptSubmit. Instinct: run the reflex here and answer with no model call.
    Reflex: inject a one-line hint so Claude runs it instead of re-deriving the steps."""
    try:
        data = json.load(sys.stdin)
        prompt, cwd = data.get("prompt", ""), data.get("cwd") or os.getcwd()
    except Exception:
        return
    if BYPASS in prompt.lower():
        return
    project = project_root(cwd)
    m = find(project, prompt)
    if not m:
        return
    scope, k, e, sc = m
    path = reflex_path(scope, e)
    approved = (e.get("instinct") and path.endswith((".sh", ".py")) and os.path.exists(path)
                and e.get("approved_sha") == file_hash(path))
    if e.get("instinct") and not approved:
        with edit(scope) as idx:  # changed since approval, or approved before v0.4: needs re-approval
            idx[k]["instinct"], idx[k]["eligible"] = False, True
    if approved and strong_match(prompt, e, k):
        try:
            r = subprocess.run(["python3" if path.endswith(".py") else "bash", path],
                               cwd=project if scope != GLOBAL else cwd,
                               capture_output=True, text=True, timeout=110)
            rc, output = r.returncode, (r.stdout + r.stderr).strip()
        except subprocess.TimeoutExpired:
            rc, output = 124, "timed out after 110s"
        if len(output) > 4000:
            output = "...\n" + output[-4000:]
        if rc == 0:
            bump(scope, k, "uses", with_args=False, quiet=True)
            print(json.dumps({"decision": "block", "reason":
                f"⚡ muscle-memory ran the instinct '{k}' ({path}) with no model call. "
                f"Add \"{BYPASS}\" to your prompt to have Claude do it instead; `mm demote {k}` turns it off."
                f"\n\n{output}"}))
            return
        bump(scope, k, "fails", quiet=True)
        print(f"[muscle-memory] The instinct '{k}' just failed (exit {rc}) and was demoted. Output:\n{output}\n"
              f"Do the task normally, then fix the reflex and re-save it with `python3 {SELF} save {k} <file>`.")
        return
    print(f"[muscle-memory] Reflex '{k}' matches this task ({e.get('summary', '')}). "
          f"Run it instead of re-deriving the steps: `python3 {SELF} run {k} [args]` (it executes the reflex and "
          f"records the outcome; no need to read the script first). Only if it fails, fall back to doing the task normally."
          + (f" This reflex is eligible to become an instinct: after the task, tell the user they can review and "
             f"approve it with `mm promote {k}`. Never promote it yourself." if e.get("eligible") else ""))


def block(reason):
    print(json.dumps({"decision": "block", "reason": "[muscle-memory] " + reason}))


def cmd_stop_hook():
    """Stop. Known task still learning: count it here, no model call. Threshold reached: ask Claude
    to compile. Brand-new task: ask Claude whether to log it, at most once per session."""
    try:
        data = json.load(sys.stdin)
        if data.get("stop_hook_active"):
            return
        prompt, calls = last_turn(data["transcript_path"])
    except Exception:
        return
    blobs = [json.dumps(c.get("input", {})) for c in calls]
    if any(re.search(r"mm\.py\W+(log|hit|fail|save|run)\b", b) for b in blobs):
        return
    work = [b for b in blobs if "mm.py" not in b]
    if len(work) < MIN_CALLS:
        return
    project = project_root(data.get("cwd"))
    m = find(project, prompt, need_reflex=False)
    if m and m[2].get("reflex"):
        return  # a reflex existed and Claude chose not to use it; nothing to learn
    if m:
        scope, k, _, _ = m
        with edit(scope) as idx:
            idx[k]["count"] += 1
            idx[k]["last_used"] = time.strftime("%Y-%m-%d")
            count = idx[k]["count"]
        if count >= THRESHOLD:
            block(f"'{k}' has now been done {count} times by hand. Compile it into a reflex as the muscle-memory "
                  f"skill describes (give every arg a default), then `python3 {SELF} save {k} <file>`"
                  + (" --global" if scope == GLOBAL else "") + ".")
        return
    if not ASK_NEW:
        return
    sid = data.get("session_id") or "unknown"
    with lock():
        p = os.path.join(ROOT, "asked.json")
        try:
            with open(p) as f:
                asked = json.load(f)
        except Exception:
            asked = []
        if sid in asked:
            return
        with open(p, "w") as f:
            json.dump((asked + [sid])[-200:], f)
    keys = ", ".join(sorted(set(read(project)) | set(read(GLOBAL)))) or "none yet"
    block(f"That task took {len(work)} tool calls. If it could recur, log it: `python3 {SELF} log <task-key> "
          f"\"<one-line summary>\" --triggers \"word,word\"` (add --global if it isn't specific to this project). "
          f"Reuse an existing key if one fits (existing: {keys}). If it's a one-off, just stop. "
          f"(Asked at most once per session.)")


# ---------- main ----------

def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        sys.exit(__doc__)
    migrate_legacy()
    is_global = "--global" in argv
    argv = [x for x in argv if x != "--global"]
    c, a = argv[0], argv[1:]
    project = project_root()
    scope = GLOBAL if is_global else project

    if c == "match":
        m = find(project, " ".join(a))
        if not m:
            print("no reflex")
            return
        s, k, e, sc = m
        print(f"REFLEX {k} ({e['reflex']}) score={sc:.2f} scope={'global' if s == GLOBAL else 'project'} "
              f"instinct={bool(e.get('instinct'))} uses={e.get('uses', 0)} fails={e.get('fails', 0)}")
        print(f"summary: {e.get('summary', '')}")
        print(f"run: python3 {SELF} run {k}")
    elif c == "log":
        trig = []
        if "--triggers" in a:
            i = a.index("--triggers")
            trig = a[i + 1].split(",") if i + 1 < len(a) else []
            a = a[:i] + a[i + 2:]
        cmd_log(scope, a[0], " ".join(a[1:]), trig)
    elif c == "save":
        cmd_save(scope, a[0], a[1])
    elif c == "run":
        cmd_run(project, a[0], a[1:])
    elif c in ("hit", "fail"):
        s, k, _ = resolve(project, a[0])
        bump(s, k, "uses" if c == "hit" else "fails")
    elif c == "promote":
        s, k, e = resolve(project, a[0])
        if not (e.get("reflex") or "").endswith((".sh", ".py")):
            sys.exit("only script reflexes (.sh/.py) can become instincts")
        path = reflex_path(s, e)
        h = file_hash(path)
        if "--confirm" not in a:
            with open(path) as f:
                body = f.read()
            print(f"Instinct review: '{k}' ({'global' if s == GLOBAL else s})\n"
                  f"Runs with NO model call when a short prompt strongly matches: {', '.join(e.get('triggers', []))}\n"
                  f"Working directory: {'your current directory' if s == GLOBAL else s}\n"
                  f"Script {path}:\n{'-' * 60}\n{body.rstrip()}\n{'-' * 60}\n"
                  f"If this is safe to run unattended, approve it with:\n  mm promote {k} --confirm {h[:12]}")
            return
        given = a[a.index("--confirm") + 1] if a.index("--confirm") + 1 < len(a) else ""
        if not given or not h.startswith(given) or len(given) < 8:
            sys.exit("hash does not match the current script; run `mm promote " + k + "` again to review it")
        with edit(s) as idx:
            idx[k].update(instinct=True, eligible=False, approved_sha=h)
        print(f"{k}: instinct ON (approved script {h[:12]}). Editing the script revokes approval.")
    elif c == "demote":
        s, k, e = resolve(project, a[0])
        with edit(s) as idx:
            idx[k]["instinct"] = False
        print(f"{k}: instinct off")
    elif c == "list":
        cmd_list(project, "--all" in a)
    elif c == "show":
        s, k, e = resolve(project, a[0])
        if not e.get("reflex"):
            sys.exit(f"'{k}' has no reflex yet")
        with open(reflex_path(s, e)) as f:
            print(f.read())
    elif c == "forget":
        s, k, e = resolve(project, a[0])
        with edit(s) as idx:
            idx.pop(k, None)
        if e.get("reflex") and os.path.exists(reflex_path(s, e)):
            os.remove(reflex_path(s, e))
        print(f"forgot {k}")
    elif c == "hook":
        cmd_prompt_hook()
    elif c == "stop-hook":
        cmd_stop_hook()
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
