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
  install | uninstall --cli codex|gemini|cursor   add/remove the skill and hooks for another agent CLI
  hook | stop-hook [--cli NAME]              hook entry points (JSON on stdin); NAME defaults to claude
"""
import hashlib
import json
import os
import re
import shlex
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
THRESHOLD = env_int("MUSCLE_MEMORY_THRESHOLD", 2)          # manual runs before compiling
INSTINCT_AFTER = env_int("MUSCLE_MEMORY_INSTINCT_AFTER", 2)  # clean no-arg reflex runs before eligible
MIN_CALLS = env_int("MUSCLE_MEMORY_MIN_CALLS", 2)          # tool calls before a turn counts as a task
ASK_NEW = os.environ.get("MUSCLE_MEMORY_ASK", "0") == "1"  # 0: new tasks are logged silently, no model turn
MAX_AUTO = 100  # silently logged candidates kept per project
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
    "save": (2, "save [--global] <key> <file> [--summary \"what it does, in general terms\"]"),
    "run": (1, "run [--global] <key> [args...]"),
    "verify": (1, "verify [--global] <key>"), "hit": (1, "hit [--global] <key>"), "fail": (1, "fail [--global] <key>"),
    "promote": (1, "promote [--global] <key>"), "demote": (1, "demote [--global] <key>"),
    "show": (1, "show [--global] <key>"), "forget": (1, "forget [--global] <key>"),
    "install": (0, "install --cli codex|gemini|cursor"), "uninstall": (0, "uninstall --cli codex|gemini|cursor"),
    "list": (0, "list [--all]"), "hook": (0, "hook [--cli claude|codex|gemini|cursor]"),
    "stop-hook": (0, "stop-hook [--cli claude|codex|gemini|cursor]"),
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
        for e in data.values():  # tolerate hand-edited entries with wrong field types
            for f, kind, default in (("triggers", list, []), ("summary", str, ""), ("phrasings", list, []),
                                     ("templates", list, []), ("approved_phrasings", list, []),
                                     ("approved_templates", list, [])):
                if f in e and not isinstance(e[f], kind):
                    e[f] = default
            for f in ("triggers", "phrasings", "templates", "approved_phrasings", "approved_templates"):
                if f in e:
                    e[f] = [x for x in e[f] if isinstance(x, str)]
            if e.get("reflex") is not None and not isinstance(e["reflex"], str):
                e["reflex"] = None
            for f in ("count", "uses", "fails", "streak"):
                if f in e and not isinstance(e[f], int):
                    e[f] = 0
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


SAFE_ARG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/:@+=-]{0,63}")
SLOT = re.compile(r"\{(\d+)\}")


def words(text):
    return [w.strip(".,!;:\"'") for w in (text or "").split() if w.strip(".,!;:\"'")]


def safe_arg(a):
    return bool(SAFE_ARG.fullmatch(a)) and ".." not in a


def make_template(prompt, args):
    """'scaffold a billing module' + ['billing'] -> 'scaffold a {1} module', or None if args aren't in it."""
    if not prompt or not args or len(args) > 4 or not all(safe_arg(a) for a in args):
        return None
    ws = words(prompt)
    if len(ws) > INSTINCT_MAX_WORDS:
        return None
    out = [w.lower() for w in ws]
    for i, a in enumerate(args, 1):
        hits = [j for j, w in enumerate(ws) if w == a]
        if len(hits) != 1 or SLOT.fullmatch(out[hits[0]]):
            return None
        out[hits[0]] = "{%d}" % i
    return " ".join(out)


def template_args(text, tpl):
    """Args if the prompt is the template word for word with safe words in the slots, else None."""
    ws, ts = words(text), tpl.split()
    if len(ws) != len(ts):
        return None
    args = {}
    for w, t in zip(ws, ts):
        m = SLOT.fullmatch(t)
        if m:
            if not safe_arg(w):
                return None
            args[int(m.group(1))] = w
        elif w.lower() != t:
            return None
    return [args[i] for i in sorted(args)]


def seq(text):
    """Content words in order: 'deploy the worker' -> ['deploy', 'worker']. Filler words are ignored, order is not."""
    return [t for t in re.findall(r"[^\W_]+", (text or "").lower()) if t not in STOP and len(t) > 1]


def instinct_match(text, entry):
    """Args for an approved instinct ([] for no-arg), or None. Fires only on a short, plain request
    whose words equal an approved phrasing's, or follow an approved template word for word."""
    if "?" in text or len(text.split()) > INSTINCT_MAX_WORDS:
        return None
    t = toks(text)
    if not t or t & NEGATIONS:
        return None
    if any(seq(text) == seq(p) for p in entry.get("approved_phrasings", [])):
        return []
    for tpl in entry.get("approved_templates", []):
        args = template_args(text, tpl)
        if args is not None:
            return args
    return None


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
            and e.get("approved_sha") == file_hash(p)
            and (e.get("approved_phrasings") or e.get("approved_templates")))


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


def cmd_save(scope, key, src, summary=None):
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
        e.update(reflex=key + ext, fails=0, streak=0, instinct=False, eligible=False, verified=False,
                 approved_sha=None, approved_phrasings=[], compile_asked=False)
        if summary:
            e["summary"] = summary.strip()[:200]
    print(f"saved reflex {dest}")


# ---------- output check: compare a script's result with what was done by hand ----------

TOKEN = re.compile(r"[a-z0-9][a-z0-9_./-]{2,}")
JUNK = re.compile(r"(^|/)(__pycache__|\.pytest_cache|\.mypy_cache|\.ruff_cache|node_modules|\.next|dist|build|"
                  r"coverage|\.tox|\.venv|\.cache)(/|$)|\.(pyc|pyo|log|tmp)$|(^|/)\.DS_Store$")


def git_out(root, *args):
    try:
        r = subprocess.run(["git", "-C", root, *args], capture_output=True, text=True, timeout=20)
        return r.stdout if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def snapshot(root):
    """Repo state before a turn or a run: HEAD, tags, untracked files and already-modified files."""
    head = (git_out(root, "rev-parse", "HEAD") or "").strip()
    if not head:
        return None
    status = git_out(root, "status", "--porcelain", "-uall") or ""
    dirty = {}
    for line in status.splitlines():
        path = line[3:]
        try:
            dirty[path] = file_hash(os.path.join(root, path))
        except OSError:
            dirty[path] = None
    return {"head": head, "tags": (git_out(root, "tag") or "").split(), "dirty": dirty, "t": time.time()}


def fingerprint(root, snap):
    """What changed since the snapshot: paths, new tags, and the words in the added content."""
    if not snap or not snap.get("head"):
        return None
    head0 = snap["head"]
    paths = set((git_out(root, "diff", "--name-only", head0) or "").split("\n")) - {""}
    status = git_out(root, "status", "--porcelain", "-uall") or ""
    new_files = [l[3:] for l in status.splitlines() if l.startswith("??")]
    paths |= set(new_files)
    text = "\n".join(l[1:] for l in (git_out(root, "diff", "-U0", head0) or "").splitlines()
                     if l.startswith("+") and not l.startswith("+++"))
    for f in new_files:
        try:
            with open(os.path.join(root, f), errors="replace") as fh:
                text += "\n" + fh.read(200_000)
        except OSError:
            pass
    for path, h in (snap.get("dirty") or {}).items():  # already changed before the turn and untouched since
        try:
            if path in paths and h == file_hash(os.path.join(root, path)):
                paths.discard(path)
        except OSError:
            pass
    text += "\n" + (git_out(root, "log", "--format=%s%n%b", f"{head0}..HEAD") or "")
    tags = sorted(set((git_out(root, "tag") or "").split()) - set(snap.get("tags") or []))
    text += "\n" + " ".join(tags)
    paths = {p for p in paths if not JUNK.search(p)}  # caches and build output aren't the task's result
    return {"paths": sorted(paths)[:200], "tags": len(tags), "tokens": sorted(set(TOKEN.findall(text.lower())))[:3000]}


def checkable(e):
    """The by-hand runs changed files or tags, so the script's result can be compared mechanically."""
    fps = [f for f in e.get("hand_fps", []) if isinstance(f, dict)]
    return fps if fps and any(f.get("paths") or f.get("tags") for f in fps) else None


def compare(fps, fp):
    """(ok, reason). Every file and every word that ALL by-hand runs produced must be in the script's result."""
    if fp is None:
        return False, "couldn't read the repo state after the run"
    need_paths = set.intersection(*(set(f.get("paths", [])) for f in fps))
    need_words = set.intersection(*(set(f.get("tokens", [])) for f in fps))
    need_tags = min(f.get("tags", 0) for f in fps)
    missing_paths = sorted(need_paths - set(fp["paths"]))
    missing_words = sorted(need_words - set(fp["tokens"]))
    problems = []
    if missing_paths:
        problems.append("didn't change " + ", ".join(missing_paths[:8]))
    if fp["tags"] < need_tags:
        problems.append(f"created {fp['tags']} git tag(s), the by-hand runs created {need_tags}")
    if missing_words:
        problems.append("its output is missing " + ", ".join(repr(w) for w in missing_words[:12])
                        + (f" and {len(missing_words) - 12} more" if len(missing_words) > 12 else ""))
    return (not problems), "; ".join(problems)


def save_snapshot(project):
    snap = snapshot(project)
    if snap:
        os.makedirs(scope_dir(project), exist_ok=True)
        write_json(os.path.join(scope_dir(project), "snapshot.json"), snap)


def load_snapshot(project):
    try:
        with open(os.path.join(scope_dir(project), "snapshot.json")) as f:
            snap = json.load(f)
        return snap if isinstance(snap, dict) and time.time() - snap.get("t", 0) < 6 * 3600 else None
    except (OSError, ValueError):
        return None


def record_hand(e, fp):
    if fp and (fp["paths"] or fp["tags"]):
        e["hand_fps"] = ([f for f in e.get("hand_fps", []) if isinstance(f, dict)] + [fp])[-3:]


def mark_eligible(e):
    """Eligible = enough clean runs, an approvable wording, AND the agent confirmed once that the output
    matched the request (exit 0 alone can't tell a wrong report from a right one)."""
    if (e.get("streak", 0) >= INSTINCT_AFTER and (e.get("phrasings") or e.get("templates"))
            and e.get("verified") and not e.get("instinct") and not e.get("eligible")):
        e["eligible"] = True
        return True
    return False


def cmd_verify(project, key, only_global):
    scope, k, e = resolve(project, key, only_global)
    if not e.get("reflex"):
        raise StoreError(f"'{k}' has no reflex to verify")
    if checkable(e):
        print(f"{k}: this task is checked automatically against the by-hand runs on every `run`; nothing to do")
        return
    with edit(scope) as idx:
        idx[k]["verified"] = True
        now = mark_eligible(idx[k])
    print(f"{k}: output confirmed correct" + (". It is now ELIGIBLE to become an instinct; tell the user they "
          f"can approve it in their own terminal with: python3 {SELF} promote {k}" if now else ""))


def bump(scope, key, field, with_args=None, quiet=False, args=None, counts=True):
    """Record a reflex outcome. with_args: None = recorded by hand, True/False = how `run` was called."""
    out = (lambda *a: None) if quiet else print
    with edit(scope) as idx:
        e = idx[key]
        e[field] = e.get(field, 0) + 1
        e["last_used"] = time.strftime("%Y-%m-%d")
        pending = e.pop("pending_prompt", None)
        if field == "uses":
            e["fails"] = 0
            if not counts:
                pass  # ran, but the result didn't match the by-hand runs: no progress toward instinct
            elif with_args is False and is_script(e):
                e["streak"] = e.get("streak", 0) + 1
                if pending and not any(seq(pending) == seq(p) for p in e.get("phrasings", [])):
                    e["phrasings"] = (e.get("phrasings", []) + [pending])[-MAX_PHRASINGS:]
            elif with_args and is_script(e):
                tpl = make_template(pending, args)  # the args were words of the prompt: learn the pattern
                if tpl:
                    e["streak"] = e.get("streak", 0) + 1
                    if tpl not in e.get("templates", []):
                        e["templates"] = (e.get("templates", []) + [tpl])[-MAX_PHRASINGS:]
                # else: args that aren't words of the prompt (e.g. defaults passed explicitly) don't count,
                # but they don't undo earlier clean runs either
            if mark_eligible(e):
                out(f"{key} is now ELIGIBLE to become an instinct. It is NOT promoted. Tell the user they can "
                    f"review and approve it in their own terminal with: python3 {SELF} promote {key}")
        else:
            e["streak"], e["instinct"], e["eligible"], e["verified"] = 0, False, False, False
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
    fps = checkable(e)
    snap = snapshot(project) if fps else None
    r = subprocess.run(["python3" if path.endswith(".py") else "bash", path] + args, cwd=cwd,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    rc, out = r.returncode, r.stdout or ""
    sys.stdout.write(out)
    sys.stdout.flush()
    counts = True
    if rc == 0:
        with edit(scope) as idx:  # shown at `promote`, so the user approves what it produces, not just the code
            idx[k]["last_output"] = {"args": args, "text": out[-1500:]}
            if fps and snap:
                ok, why = compare(fps, fingerprint(project, snap))
                idx[k]["verified"] = ok
                idx[k]["last_check"] = "matched" if ok else why[:300]
                counts = ok
        if fps and snap:
            print(f"\n[muscle-memory] output check: " + ("matches the by-hand runs." if ok else
                  f"DOESN'T match the by-hand runs: {why}. This run doesn't count toward instinct. If the user's "
                  f"request needed that, finish the task by hand, then fix the script and re-save it."))
    bump(scope, k, "uses" if rc == 0 else "fails", with_args=bool(args), args=args, counts=counts)
    return rc


def cmd_promote(project, key, only_global):
    scope, k, e = resolve(project, key, only_global)
    if not is_script(e):
        raise StoreError("only script reflexes (.sh/.py) can become instincts")
    if not (e.get("eligible") or e.get("instinct")):
        raise StoreError(f"'{k}' is not eligible yet: it needs {INSTINCT_AFTER} clean runs in a row through "
                         f"Claude, with args taken from the prompt or none (streak {e.get('streak', 0)}/{INSTINCT_AFTER})")
    if not (e.get("phrasings") or e.get("templates")):
        raise StoreError(f"'{k}' has no recorded prompts to approve yet")
    if not sys.stdin.isatty():
        raise StoreError(f"promote asks for your confirmation, so run it yourself in a terminal: "
                         f"python3 {SELF} promote {k}")
    path = reflex_path(scope, e)
    with open(path) as f:
        body = f.read()
    h = file_hash(path)
    print(f"Instinct review: '{k}' ({label(scope)})\n"
          f"It will run with NO model call when your whole prompt is one of these (same words in the same order, ignoring filler words like 'the'):")
    for p in e.get("phrasings", []):
        print(f"  - {p}")
    if e.get("templates"):
        print("or follows one of these word for word, where {1}, {2}... is any single plain word\n"
              "(letters, digits, . _ / : @ + = -) that is passed to the script as argument 1, 2...:")
        for t in e["templates"]:
            print(f"  - {t}")
    print(f"Working directory: {'wherever Claude is running' if scope == GLOBAL else scope}\n"
          f"Script {path} (sha256 {h[:12]}):\n{'-' * 60}\n{body.rstrip()}\n{'-' * 60}\n"
          + (f"Its last output{' (args: ' + ' '.join(e['last_output'].get('args') or []) + ')' if (e.get('last_output') or {}).get('args') else ''}:\n"
             f"{(e.get('last_output') or {}).get('text', '').rstrip() or '(none recorded)'}\n{'-' * 60}\n"
             if isinstance(e.get("last_output"), dict) else "")
          + f"Approval covers this file only. Anything it calls (other scripts, npm/make targets) is not pinned.")
    answer = input("Approve this instinct? Type 'yes' to approve: ").strip().lower()
    if answer != "yes":
        print("not approved")
        return 1
    with edit(scope) as idx:
        idx[k].update(instinct=True, eligible=False, approved_sha=h, approved_phrasings=list(e.get("phrasings", [])),
                       approved_templates=list(e.get("templates", [])))
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
        quiet = 0
        for k, e in sorted(idx.items(), key=lambda kv: -kv[1].get("count", 0)):
            if e.get("auto") and e.get("count", 0) < 2 and not e.get("reflex"):
                quiet += 1  # seen once, logged silently: not worth a line until it repeats
                continue
            if e.get("instinct"):
                h, nm = e.get("instinct_hits", 0), e.get("near_misses", 0)
                status = (f"INSTINCT {e['reflex']} free-runs={h} near-misses={nm}"
                          + (f" (hit rate {h * 100 // (h + nm)}%)" if h + nm else "") if approval_valid(scope, e)
                          else f"reflex {e.get('reflex')} - script changed since approval, needs re-approval")
            elif e.get("eligible"):
                status = f"reflex {e['reflex']} uses={e.get('uses', 0)} ELIGIBLE (approve: promote {k})"
            elif e.get("reflex") and is_script(e):
                status = (f"reflex {e['reflex']} uses={e.get('uses', 0)} "
                          f"instinct-in={max(INSTINCT_AFTER - e.get('streak', 0), 0)}"
                          + ("" if e.get("verified") else
                             f" output-mismatch: {e['last_check']}" if checkable(e) and e.get("last_check") else
                             "" if checkable(e) else " output-unchecked"))
            elif e.get("reflex"):
                status = f"recipe {e['reflex']} uses={e.get('uses', 0)}"
            else:
                status = f"learning ({e.get('count', 0)}/{THRESHOLD})"
            print(f"  {k:28} seen={e.get('count', 0):<3} {status}  - {e.get('summary', '')}")
        if quiet:
            print(f"  (+{quiet} one-off task{'s' if quiet > 1 else ''} seen once, hidden until repeated)")
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

CODEX_FAILED = re.compile(r"(?i)script failed|exit(?:ed with)? code:? *[1-9]|rejected by user|denied")


def codex_turn(m, state):
    """Fold one Codex rollout record into state = [prompt, calls]. Returns False if it isn't one."""
    if m.get("type") != "response_item" or not isinstance(m.get("payload"), dict):
        return False
    p = m["payload"]
    if p.get("type") == "message" and p.get("role") == "user":
        text = " ".join(c.get("text", "") for c in p.get("content") or [] if isinstance(c, dict))
        if text.strip() and not text.lstrip().startswith(("<", "# AGENTS.md")):  # skip injected context
            state[0], state[1] = text, []
    elif p.get("type") in ("function_call", "custom_tool_call", "local_shell_call"):
        if p.get("status") in (None, "completed"):
            state[1].append({"type": "tool_use", "name": p.get("name"), "id": p.get("call_id"),
                             "input": {"command": p.get("arguments") or p.get("input") or p.get("action")}})
    elif p.get("type") in ("function_call_output", "custom_tool_call_output"):
        if CODEX_FAILED.search(str(p.get("output"))[:2000]):  # failed or denied: not work done
            state[1] = [c for c in state[1] if c.get("id") != p.get("call_id")]
    return True


def last_turn(transcript_path, tail_bytes=4_000_000):
    """(prompt text, tool_use blocks) for the latest turn of a Claude Code or Codex transcript."""
    prompt, calls = "", []
    codex = ["", []]
    with open(transcript_path, "rb") as f:
        f.seek(0, os.SEEK_END)
        f.seek(max(0, f.tell() - tail_bytes))
        lines = f.read().decode("utf-8", "replace").splitlines()
    for line in lines:
        try:
            m = json.loads(line)
        except ValueError:
            continue
        if not isinstance(m, dict) or codex_turn(m, codex):
            continue
        content = (m.get("message") or {}).get("content")
        if m.get("type") == "user" and not m.get("isMeta"):
            if isinstance(content, str):
                prompt, calls = content, []
            elif isinstance(content, list) and not any(
                    isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
                prompt = " ".join(b.get("text", "") for b in content if isinstance(b, dict))
                calls = []
            elif isinstance(content, list):  # drop calls that failed or were denied: they aren't work done
                failed = {b.get("tool_use_id") for b in content
                          if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("is_error")}
                calls = [c for c in calls if c.get("id") not in failed]
        elif m.get("type") == "assistant" and isinstance(content, list):
            calls += [b for b in content if isinstance(b, dict) and b.get("type") == "tool_use"]
    return (prompt, calls) if prompt or not codex[0] else (codex[0], codex[1])


def run_instinct(path, cwd, args=()):
    p = subprocess.Popen(["python3" if path.endswith(".py") else "bash", path, *args], cwd=cwd,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)
    try:
        out, _ = p.communicate(timeout=INSTINCT_TIMEOUT)
        return p.returncode, (out or "").strip()
    except subprocess.TimeoutExpired:
        os.killpg(p.pid, signal.SIGKILL)  # the whole process group, not just the shell
        p.communicate()
        return 124, f"timed out after {INSTINCT_TIMEOUT}s"


# Each agent CLI speaks a slightly different hook protocol. Claude Code and Codex share one.
CLI = "claude"
CLIS = ("claude", "codex", "gemini", "cursor")
PROMPT_EVENT = {"claude": "UserPromptSubmit", "codex": "UserPromptSubmit", "gemini": "BeforeAgent"}


def emit_block(text):
    """Answer the prompt ourselves: the model is never called."""
    if CLI == "cursor":
        print(json.dumps({"continue": False, "user_message": text}))
    elif CLI == "gemini":
        print(json.dumps({"decision": "deny", "reason": text}))
    else:
        print(json.dumps({"decision": "block", "reason": text}))


def emit_context(text, user_msg=None):
    """Add a line to the model's context (and optionally show the user a note that costs no tokens)."""
    if CLI == "cursor":  # no context injection on beforeSubmitPrompt
        print(json.dumps({"continue": True, **({"user_message": user_msg} if user_msg else {})}))
        return
    out = {"hookSpecificOutput": {"hookEventName": PROMPT_EVENT[CLI], "additionalContext": text}}
    if user_msg:
        out["systemMessage"] = user_msg
    print(json.dumps(out))


def emit_stop(reason):
    """Ask the agent to do one more short step at the end of its turn."""
    text = "[muscle-memory] " + reason
    if CLI == "cursor":
        print(json.dumps({"followup_message": text}))
    elif CLI == "gemini":
        print(json.dumps({"decision": "deny", "reason": text}))
    else:
        print(json.dumps({"decision": "block", "reason": text}))


def done_message(k, secs, output):
    head = f"✅ Done! in {secs:.1f}s by muscle-memory, with no model call (0 tokens)."
    if CLI == "claude" and os.environ.get("MUSCLE_MEMORY_COLOR", "1") != "0":
        head = f"\033[1;32m{head}\033[0m"
    return (f"{head}\n\n{output}\n\n"
            f"— '{k}' · say \"{BYPASS}\" to have the agent do it instead")


LAUNCH = re.compile(r"^mm\s+([a-z0-9][a-z0-9-]*)(\s+.*)?$", re.S)


def launch(project, cwd, prompt):
    """`mm <key> [args]` typed as the whole prompt runs that script reflex with no model call.
    The user named the reflex explicitly, so no matching or approval is involved."""
    m = LAUNCH.match(prompt.strip())
    if not m:
        return False
    try:
        scope, k, e = resolve(project, m.group(1))
        args = shlex.split(m.group(2) or "")
    except (StoreError, ValueError):
        return False  # not a known reflex: an ordinary prompt that happens to start with "mm"
    if not is_script(e) or not os.path.exists(reflex_path(scope, e)):
        return False
    t0 = time.time()
    rc, output = run_instinct(reflex_path(scope, e), project if scope != GLOBAL else cwd, args)
    bump(scope, k, "uses" if rc == 0 else "fails", quiet=True)  # with_args=None: never counts toward instinct
    if len(output) > 4000:
        output = "...\n" + output[-4000:]
    emit_block(done_message(k, time.time() - t0, output) if rc == 0 else
               f"muscle-memory ran '{k}' (no model call), but it failed with exit {rc}:\n\n{output}")
    return True


def prompt_hook(data):
    roots = data.get("workspace_roots") if isinstance(data.get("workspace_roots"), list) else []
    prompt = data.get("prompt") or ""
    cwd = data.get("cwd") or (roots[0] if roots else None) or os.environ.get("CURSOR_PROJECT_DIR") or os.getcwd()
    if not isinstance(prompt, str) or BYPASS in prompt.lower():
        return
    project = project_root(cwd)
    save_snapshot(project)  # so the Stop hook can see what this turn changed
    if launch(project, cwd, prompt):
        return
    m = find(project, prompt)
    if not m:
        return
    scope, k, e, _ = m
    path = reflex_path(scope, e)
    if e.get("instinct") and not approval_valid(scope, e):
        with edit(scope) as idx:  # script changed since approval (or approved before v0.5): re-approve
            idx[k].update(instinct=False, eligible=bool(idx[k].get("phrasings") or idx[k].get("templates")))
    elif e.get("instinct") and instinct_match(prompt, e) is not None:
        t0 = time.time()
        rc, output = run_instinct(path, project if scope != GLOBAL else cwd, instinct_match(prompt, e))
        if len(output) > 4000:
            output = "...\n" + output[-4000:]
        if rc == 0:
            bump(scope, k, "uses", quiet=True)
            with edit(scope) as idx:
                idx[k]["instinct_hits"] = idx[k].get("instinct_hits", 0) + 1
            emit_block(done_message(k, time.time() - t0, output))
            return
        bump(scope, k, "fails", quiet=True)
        emit_context(f"[muscle-memory] The instinct '{k}' just failed (exit {rc}) and was demoted. Output:\n{output}\n"
                     f"Do the task normally, then fix the reflex and re-save it with `python3 {SELF} save {k} <file>`.")
        return
    with edit(scope) as idx:  # remember the wording; a clean `run` turns it into an approvable phrasing
        idx[k]["pending_prompt"] = prompt.strip()[:300]
        if e.get("instinct"):  # matched the task but not an approved wording: went to the model
            idx[k]["near_misses"] = idx[k].get("near_misses", 0) + 1
    hint = (f"[muscle-memory] Reflex '{k}' ({e.get('summary', '')}) may match. If that is the task, run "
            f"`python3 {SELF} run {k} [args]` as your first step"
            + (" (it's a script: pass args only for values the user actually named, exactly as they wrote them; "
               "otherwise run it with no args so its defaults apply)" if is_script(e) else "")
            + "; answer briefly from its output. If it fails, do the task normally."
            + (f" This script's output hasn't been checked yet: once, compare what it produced with exactly "
               f"what the user asked (exit 0 isn't enough). If it matches, run `python3 {SELF} verify {k}`; "
               f"if not, run `python3 {SELF} fail {k}`, do the task properly and re-save a fixed script."
               if is_script(e) and not e.get("verified") and not checkable(e) else " Don't explore or re-check what it already did."))
    note = None
    if e.get("eligible"):  # shown to the user only: costs no model tokens
        note = (f"[muscle-memory] '{k}' can run with zero tokens. To approve it, run in your "
                f"terminal: python3 {SELF} promote {k}")
    emit_context(hint, note)


def auto_log(project, prompt, fp=None):
    """Record a new multi-step task from the prompt's own words, with no model call."""
    words = [w for w in re.findall(r"[^\W_]+", prompt.lower()) if w not in STOP and len(w) > 1]
    uniq = list(dict.fromkeys(words))
    if len(uniq) < COUNT_SHARED:
        return
    summary = " ".join(prompt.split())[:120]
    with edit(project) as idx:
        key = base = slug("-".join(uniq[:4]))
        n = 2
        while key in idx:
            key, n = f"{base}-{n}", n + 1
        idx[key] = dict(new_entry(), count=1, summary=summary, triggers=uniq[:8], auto=True,
                        last_used=time.strftime("%Y-%m-%d"))
        record_hand(idx[key], fp)
        auto = sorted((v.get("last_used", ""), k) for k, v in idx.items() if v.get("auto") and not v.get("reflex"))
        for _, k in auto[:max(0, len(auto) - MAX_AUTO)]:
            del idx[k]


def block(reason):
    emit_stop(reason)


MM_CALL = re.compile(r"(mm\.py|\$\{?MM\}?)\W.*\b(log|hit|fail|save|run|verify)\b")


def stop_hook(data):
    if data.get("stop_hook_active") or data.get("loop_count"):
        return
    tp = data.get("transcript_path") or data.get("transcriptPath")
    if not tp or not os.path.isfile(tp):
        return  # this CLI didn't give us a transcript we can read: learning falls back to the skill
    prompt, calls = last_turn(tp)
    blobs = [json.dumps(c.get("input", {})) for c in calls]
    if any(MM_CALL.search(b) for b in blobs):
        return  # Claude logged, saved or used a reflex itself this turn
    work = [b for b in blobs if "mm.py" not in b and "$MM" not in b]
    if len(work) < MIN_CALLS:
        return
    roots = data.get("workspace_roots") if isinstance(data.get("workspace_roots"), list) else []
    project = project_root(data.get("cwd") or (roots[0] if roots else None) or os.environ.get("CURSOR_PROJECT_DIR"))
    m = find(project, prompt, need_reflex=False, min_overlap=COUNT_OVERLAP, min_shared=COUNT_SHARED)
    if m and m[2].get("reflex"):
        return  # a reflex existed and Claude chose not to use it; nothing to learn
    if m:
        scope, k, _, _ = m
        fp = fingerprint(project, load_snapshot(project))
        with edit(scope) as idx:
            e = idx[k]
            record_hand(e, fp)
            if e.get("count", 0) < THRESHOLD:
                e["count"] = e.get("count", 0) + 1
            e["last_used"] = time.strftime("%Y-%m-%d")
            asked = int(e.get("compile_asked") or 0)  # older stores saved True
            ask = e["count"] >= THRESHOLD and asked < 3  # re-ask if the last save didn't land
            if ask:
                e["compile_asked"] = asked + 1
        if ask:
            block(f"'{k}' ({idx[k].get('summary', '')}) has now been done {THRESHOLD} times by hand. Compile it into a reflex now, as the "
                  f"muscle-memory skill describes (give every argument a default), then `python3 {SELF} save "
                  + ("--global " if scope == GLOBAL else "") + f"{k} <file> --summary \"<what it does, in general terms, without this run's specific names>\"`"
                  + (" and check that it printed 'saved reflex'. The last attempt didn't save one." if asked else "."))
        return
    if not ASK_NEW:
        auto_log(project, prompt, fingerprint(project, load_snapshot(project)))
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


# ---------- install into other agent CLIs ----------

INSTALL = {  # cli: (skills dir, hooks file, prompt event, stop event)
    "codex": ("~/.agents/skills", "~/.codex/hooks.json", "UserPromptSubmit", "Stop"),
    "gemini": ("~/.gemini/skills", "~/.gemini/settings.json", "BeforeAgent", "AfterAgent"),
    "cursor": ("~/.cursor/skills", "~/.cursor/hooks.json", "beforeSubmitPrompt", "stop"),
}


def ours(entry):
    return "muscle-memory" in json.dumps(entry) and "mm.py" in json.dumps(entry)


def hook_entry(cli, cmd, timeout):
    if cli == "cursor":
        return {"command": cmd}
    h = {"type": "command", "command": cmd}
    if cli == "codex":
        h["timeout"] = timeout
    return {"matcher": "*", "hooks": [h]} if cli == "gemini" else {"hooks": [h]}


def cmd_install(cli, remove=False):
    if cli not in INSTALL:
        raise StoreError(f"--cli must be one of: {', '.join(INSTALL)} (Claude Code installs it as a plugin)")
    sdir, hfile, ev_prompt, ev_stop = INSTALL[cli]
    src = os.path.dirname(os.path.dirname(SELF))  # skills/muscle-memory
    dest = os.path.join(os.path.expanduser(sdir), "muscle-memory")
    hfile = os.path.expanduser(hfile)
    try:
        with open(hfile) as f:
            cfg = json.load(f)
    except FileNotFoundError:
        cfg = {}
    except ValueError as err:
        raise StoreError(f"{hfile} is not valid JSON ({err}); nothing was changed")
    if not isinstance(cfg, dict) or not isinstance(cfg.get("hooks", {}), dict):
        raise StoreError(f"{hfile} has an unexpected shape; nothing was changed")
    hooks = cfg.setdefault("hooks", {})
    if cli == "cursor":
        cfg.setdefault("version", 1)
    for ev in (ev_prompt, ev_stop):  # drop our old entries, keep everyone else's
        hooks[ev] = [e for e in hooks.get(ev, []) if not ours(e)]
        if not hooks[ev]:
            del hooks[ev]
    if not remove:
        if os.path.realpath(src) != os.path.realpath(dest):
            shutil.rmtree(dest, ignore_errors=True)
            shutil.copytree(src, dest, ignore=shutil.ignore_patterns("__pycache__"))
        mm = os.path.join(dest, "scripts", "mm.py")
        hooks.setdefault(ev_prompt, []).append(hook_entry(cli, f'python3 "{mm}" hook --cli {cli}', 120))
        hooks.setdefault(ev_stop, []).append(hook_entry(cli, f'python3 "{mm}" stop-hook --cli {cli}', 15))
    elif os.path.isfile(os.path.join(dest, "scripts", "mm.py")):
        shutil.rmtree(dest)
    os.makedirs(os.path.dirname(hfile), exist_ok=True)
    if os.path.exists(hfile) and not os.path.exists(hfile + ".before-muscle-memory"):
        shutil.copyfile(hfile, hfile + ".before-muscle-memory")
    write_json(hfile, cfg)
    if remove:
        print(f"removed muscle-memory from {cli}: hooks in {hfile}, skill in {dest}")
    else:
        print(f"installed muscle-memory for {cli}:\n  skill  {dest}\n  hooks  {hfile} ({ev_prompt}, {ev_stop})")
        if cli == "codex":
            print("  Codex asks you to trust new hooks: approve them the first time it starts.")
        print("  Your reflexes are shared with every other CLI that has it installed.")


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
    if c in ("hook", "stop-hook") and "--cli" in rest:
        global CLI
        i = rest.index("--cli")
        CLI = rest[i + 1] if i + 1 < len(rest) and rest[i + 1] in CLIS else "claude"
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
        if c in ("install", "uninstall"):
            cli = rest[rest.index("--cli") + 1] if "--cli" in rest and rest.index("--cli") + 1 < len(rest) else ""
            cmd_install(cli, remove=c == "uninstall")
        elif c == "match":
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
            summary = None
            if "--summary" in rest:
                i = rest.index("--summary")
                summary = rest[i + 1] if i + 1 < len(rest) else None
                rest = rest[:i] + rest[i + 2:]
            if len(rest) < 2:
                print(f"usage: mm.py {VERB_ARGS[c][1]}", file=sys.stderr)
                return 2
            cmd_save(scope, rest[0], rest[1], summary)
        elif c == "run":
            return cmd_run(project, rest[0], rest[1:], only_global)
        elif c == "verify":
            cmd_verify(project, rest[0], only_global)
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
