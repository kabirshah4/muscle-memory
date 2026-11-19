"""Benchmark task definitions: a fixture builder, prompts, and an output check per task.

setup(dir, git)        builds the fixture repo (git is a helper: git(dir, *args))
hand_prompts           the 3 by-hand runs, worded differently each time
repeat_prompt(run)     the shorter "do it again" request used for reflex/instinct/control runs
check(dir, run)        True if the run produced correct output
"""
import csv
import json
import os
import re
import subprocess


def _write(path, text):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


def _commit(d, git, msg):
    git(d, "add", "-A")
    git(d, "commit", "-q", "--allow-empty", "-m", msg)


# ---------- 1. repo health report ----------

def setup_report(d, git):
    for i in range(1, 5):
        _write(f"{d}/src/mod{i}.py", "\n".join(f"def f{i}_{n}():\n    return {n}" for n in range(i * 20)) + "\n")
    for i in range(1, 3):
        _write(f"{d}/web/app{i}.ts", "\n".join(f"export const v{n} = {n};" for n in range(i * 60)) + "\n")
    _write(f"{d}/README.md", "# demo\n")
    _commit(d, git, "feat: add modules")
    _write(f"{d}/README.md", "# demo\n\nA demo project.\n")
    _commit(d, git, "docs: readme")
    _write(f"{d}/src/mod1.py", open(f"{d}/src/mod1.py").read() + "X = 1\n")
    _commit(d, git, "fix: mod1 constant")


def check_report(d, run):
    p = os.path.join(d, "REPORT.md")
    if not os.path.exists(p):
        return False
    t = open(p).read().lower()
    return "python" in t and "typescript" in t and "mod4.py" in t and "feat" in t


# ---------- 2. changelog since last tag ----------

def setup_changelog(d, git):
    _write(f"{d}/app.py", "print('v1')\n")
    _write(f"{d}/CHANGELOG.md", "# Changelog\n\n## v0.1.0\n\n- Initial release\n")
    _commit(d, git, "feat: initial release")
    git(d, "tag", "v0.1.0")
    for msg in ("feat: add export command", "fix: crash on empty input", "docs: usage examples",
                "feat(api): pagination support", "chore: bump deps", "fix(cli): exit code on error"):
        _write(f"{d}/app.py", open(f"{d}/app.py").read() + f"# {msg}\n")
        _commit(d, git, msg)


def check_changelog(d, run):
    t = open(os.path.join(d, "CHANGELOG.md")).read()
    lower = t.lower()
    return ("export command" in lower and "pagination" in lower and "crash on empty input" in lower
            and "v0.1.0" in lower and t.count("Initial release") == 1)


# ---------- 3. CSV -> JSON summary ----------

def setup_csv(d, git):
    _write(f"{d}/data/sales.csv", "region,units,price\n" + "\n".join(
        f"{r},{u},{p}" for r, u, p in [("north", 12, 9.5), ("south", 7, 11.0), ("east", 20, 8.25),
                                       ("west", 3, 15.0), ("north", 9, 9.5)]) + "\n")
    _write(f"{d}/data/users.csv", "name,age,plan\nana,31,pro\nbo,24,free\ncy,45,pro\n")
    _commit(d, git, "data: add csv files")


def check_csv(d, run):
    p = os.path.join(d, "data", "summary.json")
    try:
        s = json.load(open(p))
    except Exception:
        return False
    blob = json.dumps(s).lower()
    # row counts and a known numeric stat must appear somewhere in the structure
    return "sales" in blob and "users" in blob and "5" in blob and "3" in blob and ("20" in blob or "20.0" in blob)


# ---------- 4. scaffold a module (needs an argument -> stays a reflex) ----------

NAMES = ["billing", "invoices", "reports", "alerts", "exports", "imports", "search", "audit", "quota"]


def setup_scaffold(d, git):
    _write(f"{d}/src/__init__.py", "")
    _write(f"{d}/src/core.py", '"""Core module."""\n\n\ndef run():\n    return "core"\n')
    _write(f"{d}/tests/__init__.py", "")
    _write(f"{d}/tests/test_core.py", "import unittest\nfrom src import core\n\n\nclass T(unittest.TestCase):\n"
                                      "    def test_run(self):\n        self.assertEqual(core.run(), 'core')\n")
    _commit(d, git, "feat: core module")


def check_scaffold(d, run):
    name = NAMES[run - 1]
    if not (os.path.exists(f"{d}/src/{name}.py") and os.path.exists(f"{d}/tests/test_{name}.py")):
        return False
    r = subprocess.run(["python3", "-m", "unittest", "discover", "-s", "tests", "-t", "."], cwd=d,
                       capture_output=True, text=True)
    return r.returncode == 0


TASKS = {
    "report": dict(
        setup=setup_report, check=check_report,
        hand_prompts=[
            "Write a REPORT.md project health report for this repo: lines of code per language, the 3 largest "
            "source files, the last 5 commits grouped by conventional-commit type, and a total file count.",
            "Create REPORT.md with a health report of this repository: LOC by language, three biggest files, "
            "recent commits grouped by type (feat/fix/docs), number of files.",
            "Generate the repo health report into REPORT.md (languages + line counts, largest 3 files, last 5 "
            "commits by conventional type, file total).",
        ],
        repeat_prompt=lambda run: "regenerate the repo health report in REPORT.md",
    ),
    "changelog": dict(
        setup=setup_changelog, check=check_changelog,
        hand_prompts=[
            "Update CHANGELOG.md with every commit since the last git tag, grouped into Features, Fixes and "
            "Documentation, under a new 'Unreleased' heading above the existing entries. Skip chore commits.",
            "Add the commits since the latest tag to CHANGELOG.md as an Unreleased section grouped by type "
            "(Features / Fixes / Documentation). Leave older entries untouched and ignore chores.",
            "Prepend an Unreleased section to CHANGELOG.md listing commits since the last tag, grouped by "
            "conventional-commit type (features, fixes, docs; no chores).",
        ],
        repeat_prompt=lambda run: "update the changelog since the last tag",
    ),
    "csv": dict(
        setup=setup_csv, check=check_csv,
        hand_prompts=[
            "Summarize every CSV in data/ into data/summary.json: for each file its row count, its column "
            "names, and min/max/mean for each numeric column.",
            "Write data/summary.json describing each CSV under data/: number of rows, columns, and min, max and "
            "mean of every numeric column.",
            "Produce data/summary.json from the CSV files in data/ (per file: rows, columns, numeric min/max/mean).",
        ],
        repeat_prompt=lambda run: "redo the csv summary in data/summary.json",
    ),
    "scaffold": dict(
        setup=setup_scaffold, check=check_scaffold,
        hand_prompts=[
            f"Create a new Python module src/{NAMES[0]}.py with a module docstring and a run() function that "
            f"returns the string '{NAMES[0]}', plus tests/test_{NAMES[0]}.py with one passing unittest for it, "
            "following the style of src/core.py and tests/test_core.py.",
            f"Scaffold a module named {NAMES[1]}: src/{NAMES[1]}.py (docstring, run() returning '{NAMES[1]}') and "
            f"tests/test_{NAMES[1]}.py with a passing unittest, matching the existing core module's style.",
            f"Add module {NAMES[2]} like core: src/{NAMES[2]}.py with run() returning '{NAMES[2]}' and a "
            f"unittest in tests/test_{NAMES[2]}.py.",
        ],
        repeat_prompt=lambda run: f"scaffold a {NAMES[run - 1]} module with a test",
    ),
}
