#!/usr/bin/env python3
"""muscle-memory: count repeated tasks, compile them into reflexes, recall them cheaply.

Reflexes belong to the project (git repository, shared across its worktrees; else the
working directory) they were learned in. Pass --global to log/save one for everywhere.

Store (~/.claude/muscle-memory/ or $MUSCLE_MEMORY_DIR):
  projects/<name>-<hash>/index.json, reflexes/   per project
  global/index.json, reflexes/                   everywhere

Commands:
  match "<task text>"                        best reflex for this task here, or nothing
  log [--global] <key> "<summary>" [--triggers a,b]   record one manual run
  save [--global] <key> <file>               install a reflex (.sh, .py or .md)
  run [--global] <key> [args...]             execute a reflex (from the project root), record the outcome
  hit | fail [--global] <key>                record a reflex outcome by hand
  promote [--global] <key>                   (you, in a terminal) review and approve an instinct
  demote [--global] <key>                    turn an instinct off
  list [--all]                               tasks here + global (--all: every project)
  show | forget [--global] <key>             print / delete a reflex
  hook | stop-hook                           Claude Code hook entry points (JSON on stdin)
"""
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager

try:
    import fcntl
except ImportError:  # Windows is unsupported; run without locking rather than crash
    fcntl = None


def env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


ROOT = os.path.expanduser(os.environ.get("MUSCLE_MEMORY_DIR") or "~/.claude/muscle-memory")
THRESHOLD = env_int("MUSCLE_MEMORY_THRESHOLD", 3)          # manual runs before compiling
INSTINCT_AFTER = env_int("MUSCLE_MEMORY_INSTINCT_AFTER", 3)  # clean no-arg reflex runs before eligible
MIN_CALLS = env_int("MUSCLE_MEMORY_MIN_CALLS", 3)          # tool calls before a turn counts as a task
ASK_NEW = os.environ.get("MUSCLE_MEMORY_ASK", "1") != "0"
# Overlap = shared words / the smaller of (prompt words, task words), so long detailed prompts still match.
HINT_OVERLAP, HINT_SHARED = 0.5, 2     # to suggest a reflex to Claude
COUNT_OVERLAP, COUNT_SHARED = 0.6, 3   # stricter, to silently count a turn as a known task
INSTINCT_MAX_WORDS = 12
INSTINCT_TIMEOUT = 110
MAX_PHRASINGS = 10
BYPASS = "no reflex"
SELF = os.path.abspath(__file__)
GLOBAL = "global"
STOP = set((
    "a an the to of for and or in on at with my me i it this that please can you do make run "
    "give show get again same usual last time now just want need let lets us could would will hey ok"
).split())
# Words that change what a prompt asks for. An instinct never fires on a prompt containing one.
NEGATIONS = set((
    "not no don dont never without stop cancel undo revert rollback roll back disable skip "
    "why how what when where which explain debug fix broken fails failing failed delete remove"
).split())
VERB_ARGS = {  # command -> (minimum positional args, usage)
    "match": (1, 'match "<task text>"'),
    "log": (1, 'log [--global] <key> "<summary>" [--triggers a,b]'),
    "save": (2, "save [--global] <key> <file>"),
    "run": (1, "run [--global] <key> [args...]"),
    "hit": (1, "hit [--global] <key>"), "fail": (1, "fail [--global] <key>"),
    "promote": (1, "promote [--global] <key>"), "demote": (1, "demote [--global] <key>"),
    "show": (1, "show [--global] <key>"), "forget": (1, "forget [--global] <key>"),
    "list": (0, "list [--all]"), "hook": (0, "hook"), "stop-hook": (0, "stop-hook"),
}


class StoreError(Exception):
    pass


# ---------- scopes & storage ----------

def project_root(path=None):
    """The repository root (the main checkout, so worktrees share reflexes), else the directory."""
    path = os.path.realpath(path or os.getcwd())
    try:
        r = subprocess.run(["git", "-C", path, "rev-parse", "--git-common-dir", "--show-toplevel"],
                           capture_output=True, text=True, timeout=3)
        if r.returncode == 0:
            common, top = (r.stdout.strip().splitlines() + ["", ""])[:2]
            common = os.path.realpath(os.path.join(path, common))
            if os.path.basename(common) == ".git":
                return os.path.dirname(common)
            if top:
                return os.path.realpath(top)
    except (OSError, subprocess.SubprocessError):
        pass
    return path


def scope_dir(scope):
    if scope == GLOBAL:
        return os.path.join(ROOT, GLOBAL)
    name = re.sub(r"[^a-zA-Z0-9]+", "-", os.path.basename(scope)).strip("-")[:40] or "root"
    return os.path.join(ROOT, "projects", f"{name}-{hashlib.sha1(scope.encode()).hexdigest()[:8]}")


def label(scope):
    return "global" if scope == GLOBAL else scope


def reflex_path(scope, e):
    return os.path.join(scope_dir(scope), "reflexes", e["reflex"])


def read(scope, strict=False):
    """The scope's index. Non-strict (hooks, matching) skips anything unreadable; strict raises."""
    p = os.path.join(scope_dir(scope), "index.json")
    if not os.path.exists(p):
        return {}
    try:
        with open(p) as f:
            data = json.load(f)
        if not isinstance(data, dict) or not all(isinstance(v, dict) for v in data.values()):
            raise ValueError("expected an object of task objects")
    except (OSError, ValueError) as err:
        if strict:
            raise StoreError(f"{p} is unreadable ({err}). Nothing was changed. Fix it, or move it "
                             f"aside to start this scope fresh.")
        return {}
    return data


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


def write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


@contextmanager
def edit(scope):
    """Locked read-modify-write of one scope's index. Refuses to overwrite an unreadable index."""
    with lock():
        idx = read(scope, strict=True)
        yield idx
        d = scope_dir(scope)
        os.makedirs(os.path.join(d, "reflexes"), exist_ok=True)
        write_json(os.path.join(d, "scope.json"), {"root": scope})
        write_json(os.path.join(d, "index.json"), idx)


def migrate_legacy():
    """v0.1/v0.2 kept one flat store. Merge it into global (existing entries win), instincts off."""
    old = os.path.join(ROOT, "index.json")
    if not os.path.exists(old):
        return
    with lock():
        if not os.path.exists(old):
            return
        try:
            with open(old) as f:
                legacy = json.load(f)
            if not isinstance(legacy, dict):
                raise ValueError("not an object")
            gd = scope_dir(GLOBAL)
            os.makedirs(os.path.join(gd, "reflexes"), exist_ok=True)
            idx = read(GLOBAL, strict=True)
            for k, e in legacy.items():
                if isinstance(e, dict) and k not in idx:
                    e.update(instinct=False, eligible=False, streak=0)
                    idx[k] = e
            legacy_dir = os.path.join(ROOT, "reflexes")
            if os.path.isdir(legacy_dir):
                for name in os.listdir(legacy_dir):
                    dest = os.path.join(gd, "reflexes", name)
                    if not os.path.exists(dest):
                        shutil.move(os.path.join(legacy_dir, name), dest)
            write_json(os.path.join(gd, "index.json"), idx)
            os.replace(old, old + ".migrated")
        except (OSError, ValueError, StoreError) as err:
            aside = f"{old}.corrupt-{int(time.time())}"
            os.replace(old, aside)
            print(f"muscle-memory: legacy store unreadable ({err}); moved to {aside}", file=sys.stderr)


# ---------- matching ----------

def toks(text):
    return {t for t in re.findall(r"[^\W_]+", (text or "").lower()) if t not in STOP and len(t) > 1}


def vocab(entry, key):
    return toks(" ".join(entry.get("triggers", [])) + " " + key.replace("-", " ") + " " + entry.get("summary", ""))


def overlap(text, entry, key):
    """(shared words / the smaller word set, number of shared words)."""
    t, v = toks(text), vocab(entry, key)
    shared = t & v
    return (len(shared) / min(len(t), len(v)) if t and v else 0.0), len(shared)


def is_match(text, entry, key, min_overlap, min_shared):
    ov, shared = overlap(text, entry, key)
    return ov >= min_overlap and shared >= min_shared


def instinct_match(text, entry):
    """An instinct fires only on a short, plain request whose words equal an approved phrasing's."""
    if "?" in text or len(text.split()) > INSTINCT_MAX_WORDS:
        return False
    t = toks(text)
    if not t or t & NEGATIONS:
        return False
    return any(t == toks(p) for p in entry.get("approved_phrasings", []))


def find(project, text, need_reflex=True, min_overlap=HINT_OVERLAP, min_shared=HINT_SHARED):
    """Best (scope, key, entry, overlap) in this project or global; project wins ties."""
    best = None
    for scope in (project, GLOBAL):
        for k, e in read(scope).items():
            if need_reflex and not e.get("reflex"):
                continue
            if is_match(text, e, k, min_overlap, min_shared):
                cov = overlap(text, e, k)[0]
                if best is None or cov > best[3]:
                    best = (scope, k, e, cov)
    return best


def slug(key):
    s = re.sub(r"[^a-z0-9-]+", "-", key.lower()).strip("-")
    if not s:
        raise StoreError("key must contain letters or digits")
    return s


def resolve(project, key, only_global=False):
    k = slug(key)
    for scope in ((GLOBAL,) if only_global else (project, GLOBAL)):
        e = read(scope, strict=True).get(k)
        if e is not None:
            return scope, k, e
    raise StoreError(f"unknown task '{k}'{' in global' if only_global else ' in this project or global'} "
                     f"(see: python3 {SELF} list)")


def new_entry():
    return {"count": 0, "triggers": [], "summary": "", "reflex": None, "uses": 0, "fails": 0}


def is_script(e):
    return (e.get("reflex") or "").endswith((".sh", ".py"))


def file_hash(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def approval_valid(scope, e):
    p = reflex_path(scope, e) if e.get("reflex") else ""
    return (e.get("instinct") and is_script(e) and os.path.exists(p)
            and e.get("approved_sha") == file_hash(p) and e.get("approved_phrasings"))


# ---------- commands ----------

def cmd_log(scope, key, summary, triggers):
    key = slug(key)
    with edit(scope) as idx:
        e = idx.setdefault(key, new_entry())
        e["count"] = e.get("count", 0) + 1
        e["summary"] = summary or e.get("summary", "")
        e["triggers"] = sorted(set(e.get("triggers", [])) | {x.strip().lower() for x in triggers if x.strip()})
        e["last_used"] = time.strftime("%Y-%m-%d")
    print(f"{key}: seen {e['count']}x ({'global' if scope == GLOBAL else 'this project'})")
    if e.get("reflex"):
        print(f"reflex already exists - next time run it: python3 {SELF} run {key}")
    elif e["count"] >= THRESHOLD:
        print(f"COMPILE NOW: threshold {THRESHOLD} reached. Write the reflex and run: python3 {SELF} save "
              + ("--global " if scope == GLOBAL else "") + f"{key} <file>")
    else:
        print(f"{THRESHOLD - e['count']} more run(s) before compiling")


def cmd_save(scope, key, src):
    key = slug(key)
    ext = os.path.splitext(src)[1]
    if ext not in (".sh", ".py", ".md"):
        raise StoreError("reflex must be a .sh, .py or .md file")
    if not os.path.isfile(src):
        raise StoreError(f"no such file: {src}")
    with edit(scope) as idx:
        rdir = os.path.join(scope_dir(scope), "reflexes")
        os.makedirs(rdir, exist_ok=True)
        dest = os.path.join(rdir, key + ext)
        if os.path.realpath(src) != os.path.realpath(dest):
            fd, tmp = tempfile.mkstemp(dir=rdir, prefix=f".{key}.")
            os.close(fd)
            shutil.copyfile(src, tmp)
            os.replace(tmp, dest)
        if ext != ".md":
            os.chmod(dest, 0o755)
        for old in os.listdir(rdir):  # drop other variants (.py/.md/.broken) only after the copy succeeded
            if old != key + ext and old.split(".")[0] == key:
                os.remove(os.path.join(rdir, old))
        e = idx.setdefault(key, new_entry())
        e.update(reflex=key + ext, fails=0, streak=0, instinct=False, eligible=False,
                 approved_sha=None, approved_phrasings=[], compile_asked=False)
    print(f"saved reflex {dest}")


def bump(scope, key, field, with_args=None, quiet=False):
    """Record a reflex outcome. with_args: None = recorded by hand, True/False = how `run` was called."""
    out = (lambda *a: None) if quiet else print
    with edit(scope) as idx:
        e = idx[key]
        e[field] = e.get(field, 0) + 1
        e["last_used"] = time.strftime("%Y-%m-%d")
        pending = e.pop("pending_prompt", None)
        if field == "uses":
            e["fails"] = 0
            if with_args is False and is_script(e):
                e["streak"] = e.get("streak", 0) + 1
                if pending and not any(toks(pending) == toks(p) for p in e.get("phrasings", [])):
                    e["phrasings"] = (e.get("phrasings", []) + [pending])[-MAX_PHRASINGS:]
            elif with_args:
                e["streak"] = 0
            if (e.get("streak", 0) >= INSTINCT_AFTER and e.get("phrasings")
                    and not e.get("instinct") and not e.get("eligible")):
                e["eligible"] = True
                out(f"{key} is now ELIGIBLE to become an instinct. It is NOT promoted. Tell the user they can "
                    f"review and approve it in their own terminal with: python3 {SELF} promote {key}")
        else:
            e["streak"], e["instinct"], e["eligible"] = 0, False, False
            if e["fails"] >= 2 and e.get("reflex"):
                out(f"{key} failed twice in a row - reflex disabled, fix and re-save it")
                p = reflex_path(scope, e)
                if os.path.exists(p):
                    os.replace(p, p + ".broken")
                e["reflex"] = None
    out(f"{key}: uses={e.get('uses', 0)} fails={e.get('fails', 0)}")


def cmd_run(project, key, args, only_global):
    scope, k, e = resolve(project, key, only_global)
    if not e.get("reflex"):
        raise StoreError(f"'{k}' has no reflex yet")
    path = reflex_path(scope, e)
    if not os.path.exists(path):
        bump(scope, k, "fails")
        raise StoreError(f"reflex file missing: {path}")
    if path.endswith(".md"):
        with open(path) as f:
            print(f.read())
        print(f"(recipe reflex: follow it, then run `python3 {SELF} hit {k}` or `fail {k}`)")
        return 0
    cwd = scope if scope != GLOBAL else os.getcwd()  # same directory an instinct would run in
    rc = subprocess.call(["python3" if path.endswith(".py") else "bash", path] + args, cwd=cwd)
    bump(scope, k, "uses" if rc == 0 else "fails", with_args=bool(args))
    return rc


def cmd_promote(project, key, only_global):
    scope, k, e = resolve(project, key, only_global)
    if not is_script(e):
        raise StoreError("only script reflexes (.sh/.py) can become instincts")
    if not (e.get("eligible") or e.get("instinct")):
        raise StoreError(f"'{k}' is not eligible yet: it needs {INSTINCT_AFTER} clean runs in a row through "
                         f"Claude, with no args (streak {e.get('streak', 0)}/{INSTINCT_AFTER})")
    if not e.get("phrasings"):
        raise StoreError(f"'{k}' has no recorded prompts to approve yet")
    if not sys.stdin.isatty():
        raise StoreError(f"promote asks for your confirmation, so run it yourself in a terminal: "
                         f"python3 {SELF} promote {k}")
    path = reflex_path(scope, e)
    with open(path) as f:
        body = f.read()
    h = file_hash(path)
    print(f"Instinct review: '{k}' ({label(scope)})\n"
          f"It will run with NO model call when your whole prompt is one of these (ignoring filler words):")
    for p in e["phrasings"]:
        print(f"  - {p}")
    print(f"Working directory: {'wherever Claude is running' if scope == GLOBAL else scope}\n"
          f"Script {path} (sha256 {h[:12]}):\n{'-' * 60}\n{body.rstrip()}\n{'-' * 60}\n"
          f"Approval covers this file only. Anything it calls (other scripts, npm/make targets) is not pinned.")
    answer = input("Approve this instinct? Type 'yes' to approve: ").strip().lower()
    if answer != "yes":
        print("not approved")
        return 1
    with edit(scope) as idx:
        idx[k].update(instinct=True, eligible=False, approved_sha=h, approved_phrasings=list(e["phrasings"]))
    print(f"{k}: instinct ON. Editing or re-saving the script revokes approval.")
    return 0


def cmd_list(project, all_projects):
    scopes = [project, GLOBAL]
    pdir = os.path.join(ROOT, "projects")
    if all_projects and os.path.isdir(pdir):
        for d in sorted(os.listdir(pdir)):
            try:
                with open(os.path.join(pdir, d, "scope.json")) as f:
                    root = json.load(f)["root"]
            except (OSError, ValueError, KeyError, TypeError):
                continue
            if root not in scopes:
                scopes.append(root)
    shown = False
    for scope in scopes:
        idx = read(scope, strict=True)
        if not idx:
            continue
        shown = True
        print(f"[{label(scope)}]")
        for k, e in sorted(idx.items(), key=lambda kv: -kv[1].get("count", 0)):
            if e.get("instinct"):
                status = (f"INSTINCT {e['reflex']} uses={e.get('uses', 0)}" if approval_valid(scope, e)
                          else f"reflex {e.get('reflex')} - script changed since approval, needs re-approval")
            elif e.get("eligible"):
                status = f"reflex {e['reflex']} uses={e.get('uses', 0)} ELIGIBLE (approve: promote {k})"
            elif e.get("reflex") and is_script(e):
                status = (f"reflex {e['reflex']} uses={e.get('uses', 0)} "
                          f"instinct-in={max(INSTINCT_AFTER - e.get('streak', 0), 0)}")
            elif e.get("reflex"):
                status = f"recipe {e['reflex']} uses={e.get('uses', 0)}"
            else:
                status = f"learning ({e.get('count', 0)}/{THRESHOLD})"
            print(f"  {k:28} seen={e.get('count', 0):<3} {status}  - {e.get('summary', '')}")
    if not shown:
        print("no tasks recorded yet")


def cmd_forget(project, key, only_global):
    scope, k, e = resolve(project, key, only_global)
    with edit(scope) as idx:
        idx.pop(k, None)
    rdir = os.path.join(scope_dir(scope), "reflexes")
    for name in os.listdir(rdir) if os.path.isdir(rdir) else []:
        if name.split(".")[0] == k:
            os.remove(os.path.join(rdir, name))
    print(f"forgot {k} ({label(scope)})")


# ---------- hooks (must never break Claude Code: every failure is silent) ----------

def last_turn(transcript_path, tail_bytes=4_000_000):
    """(prompt text, tool_use blocks) for the latest turn of a Claude Code transcript."""
    prompt, calls = "", []
    with open(transcript_path, "rb") as f:
        f.seek(0, os.SEEK_END)
        f.seek(max(0, f.tell() - tail_bytes))
        lines = f.read().decode("utf-8", "replace").splitlines()
    for line in lines:
        try:
            m = json.loads(line)
        except ValueError:
            continue
        if not isinstance(m, dict):
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


def run_instinct(path, cwd):
    p = subprocess.Popen(["python3" if path.endswith(".py") else "bash", path], cwd=cwd,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)
    try:
        out, _ = p.communicate(timeout=INSTINCT_TIMEOUT)
        return p.returncode, (out or "").strip()
    except subprocess.TimeoutExpired:
        os.killpg(p.pid, signal.SIGKILL)  # the whole process group, not just the shell
        p.communicate()
        return 124, f"timed out after {INSTINCT_TIMEOUT}s"


def prompt_hook(data):
    prompt, cwd = data.get("prompt") or "", data.get("cwd") or os.getcwd()
    if BYPASS in prompt.lower():
        return
    project = project_root(cwd)
    m = find(project, prompt)
    if not m:
        return
    scope, k, e, _ = m
    path = reflex_path(scope, e)
    if e.get("instinct") and not approval_valid(scope, e):
        with edit(scope) as idx:  # script changed since approval (or approved before v0.5): re-approve
            idx[k].update(instinct=False, eligible=bool(idx[k].get("phrasings")))
    elif e.get("instinct") and instinct_match(prompt, e):
        rc, output = run_instinct(path, project if scope != GLOBAL else cwd)
        if len(output) > 4000:
            output = "...\n" + output[-4000:]
        if rc == 0:
            bump(scope, k, "uses", quiet=True)
            print(json.dumps({"decision": "block", "reason":
                f"⚡ muscle-memory ran the instinct '{k}' ({path}) with no model call; Claude didn't see "
                f"this prompt or its output. Add \"{BYPASS}\" to a prompt to have Claude do it instead.\n\n{output}"}))
            return
        bump(scope, k, "fails", quiet=True)
        print(f"[muscle-memory] The instinct '{k}' just failed (exit {rc}) and was demoted. Output:\n{output}\n"
              f"Do the task normally, then fix the reflex and re-save it with `python3 {SELF} save {k} <file>`.")
        return
    with edit(scope) as idx:  # remember the wording; a clean `run` turns it into an approvable phrasing
        idx[k]["pending_prompt"] = prompt.strip()[:300]
    hint = (f"[muscle-memory] Reflex '{k}' ({e.get('summary', '')}) may match this prompt. If the user is "
            f"asking you to perform that task, run it instead of re-deriving the steps: "
            f"`python3 {SELF} run {k} [args]` (it executes the reflex and records the outcome). "
            f"If it fails, do the task normally.")
    if e.get("eligible"):
        hint += (f" This reflex is eligible to become an instinct: afterwards, tell the user they can approve it "
                 f"in their own terminal with `python3 {SELF} promote {k}`. Never run promote yourself.")
    print(hint)


def block(reason):
    print(json.dumps({"decision": "block", "reason": "[muscle-memory] " + reason}))


MM_CALL = re.compile(r"(mm\.py|\$\{?MM\}?)\W.*\b(log|hit|fail|save|run)\b")


def stop_hook(data):
    if data.get("stop_hook_active"):
        return
    prompt, calls = last_turn(data["transcript_path"])
    blobs = [json.dumps(c.get("input", {})) for c in calls]
    if any(MM_CALL.search(b) for b in blobs):
        return  # Claude logged, saved or used a reflex itself this turn
    work = [b for b in blobs if "mm.py" not in b and "$MM" not in b]
    if len(work) < MIN_CALLS:
        return
    project = project_root(data.get("cwd"))
    m = find(project, prompt, need_reflex=False, min_overlap=COUNT_OVERLAP, min_shared=COUNT_SHARED)
    if m and m[2].get("reflex"):
        return  # a reflex existed and Claude chose not to use it; nothing to learn
    if m:
        scope, k, _, _ = m
        with edit(scope) as idx:
            e = idx[k]
            if e.get("count", 0) < THRESHOLD:
                e["count"] = e.get("count", 0) + 1
            e["last_used"] = time.strftime("%Y-%m-%d")
            ask = e["count"] >= THRESHOLD and not e.get("compile_asked")
            if ask:
                e["compile_asked"] = True
        if ask:
            block(f"'{k}' has now been done {THRESHOLD} times by hand. Compile it into a reflex now, as the "
                  f"muscle-memory skill describes (give every argument a default), then `python3 {SELF} save "
                  + ("--global " if scope == GLOBAL else "") + f"{k} <file>`.")
        return
    if not ASK_NEW:
        return
    sid = data.get("session_id") or "unknown"
    with lock():
        p = os.path.join(ROOT, "asked.json")
        try:
            with open(p) as f:
                asked = json.load(f)
            if not isinstance(asked, list):
                asked = []
        except (OSError, ValueError):
            asked = []
        if sid in asked:
            return
        write_json(p, (asked + [sid])[-200:])
    keys = ", ".join(sorted(set(read(project)) | set(read(GLOBAL)))) or "none yet"
    block(f"That task took {len(work)} tool calls. If it could recur, log it: `python3 {SELF} log <task-key> "
          f"\"<one-line summary>\" --triggers \"word,word\"` (add --global if it isn't specific to this project). "
          f"Reuse an existing key if one fits (existing: {keys}). If it's a one-off, just stop. "
          f"(Asked at most once per session.)")


def run_hook(fn):
    try:
        data = json.load(sys.stdin)
        if isinstance(data, dict):
            fn(data)
    except Exception:  # a broken store or odd input must never break the user's session
        pass


# ---------- main ----------

def main(argv):
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    c, rest = argv[0], argv[1:]
    if c not in VERB_ARGS:
        print(f"unknown command '{c}'\n{__doc__}", file=sys.stderr)
        return 2
    if c == "hook":
        migrate_legacy()
        run_hook(prompt_hook)
        return 0
    if c == "stop-hook":
        migrate_legacy()
        run_hook(stop_hook)
        return 0
    # --global is a flag of ours only before the key; for `run`, anything after the key goes to the script
    only_global = False
    if c == "run":
        if rest[:1] == ["--global"]:
            only_global, rest = True, rest[1:]
    elif "--global" in rest:
        only_global, rest = True, [x for x in rest if x != "--global"]
    if len(rest) < VERB_ARGS[c][0]:
        print(f"usage: mm.py {VERB_ARGS[c][1]}", file=sys.stderr)
        return 2
    try:
        migrate_legacy()
        project = project_root()
        scope = GLOBAL if only_global else project
        if c == "match":
            m = find(project, " ".join(rest))
            if not m:
                print("no reflex")
                return 0
            s, k, e, cov = m
            print(f"REFLEX {k} ({e['reflex']}) overlap={cov:.2f} scope={'global' if s == GLOBAL else 'project'} "
                  f"instinct={bool(e.get('instinct'))} uses={e.get('uses', 0)} fails={e.get('fails', 0)}")
            print(f"summary: {e.get('summary', '')}")
            print(f"run: python3 {SELF} run {k}")
        elif c == "log":
            trig = []
            if "--triggers" in rest:
                i = rest.index("--triggers")
                trig = rest[i + 1].split(",") if i + 1 < len(rest) else []
                rest = rest[:i] + rest[i + 2:]
            cmd_log(scope, rest[0], " ".join(rest[1:]), trig)
        elif c == "save":
            cmd_save(scope, rest[0], rest[1])
        elif c == "run":
            return cmd_run(project, rest[0], rest[1:], only_global)
        elif c in ("hit", "fail"):
            s, k, _ = resolve(project, rest[0], only_global)
            bump(s, k, "uses" if c == "hit" else "fails")
        elif c == "promote":
            return cmd_promote(project, rest[0], only_global)
        elif c == "demote":
            s, k, _ = resolve(project, rest[0], only_global)
            with edit(s) as idx:
                idx[k]["instinct"] = False
            print(f"{k}: instinct off")
        elif c == "list":
            cmd_list(project, "--all" in rest)
        elif c == "show":
            s, k, e = resolve(project, rest[0], only_global)
            if not e.get("reflex"):
                raise StoreError(f"'{k}' has no reflex yet")
            with open(reflex_path(s, e)) as f:
                print(f.read())
        elif c == "forget":
            cmd_forget(project, rest[0], only_global)
    except StoreError as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
