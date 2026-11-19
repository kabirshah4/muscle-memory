---
name: muscle-memory
description: Use when starting any multi-step task that feels routine or familiar (setup, deploy, scaffold, export, convert, release, cleanup, report), when a "[muscle-memory] Reflex ... matches" hint appears, when the user says "do the usual", "same as last time" or "again", or when a task was just finished that could recur.
---

# Muscle Memory

## Overview
Repeated tasks shouldn't be re-reasoned from scratch. Track how often a task recurs; after 3 runs, compile it into a **reflex** (a parameterized script or a terse recipe). After that, recall the reflex and execute it with no exploring or re-planning.

`MM=<this skill's base directory>/scripts/mm.py`. Reflexes belong to the **project** they were learned in (its git root) and only match there. Add `--global` to `log`/`save` for tasks that aren't tied to one project.

## Flow

1. **Recall (before doing anything else):** `python3 $MM match "<one-line task description>"`
   - **REFLEX found:** `python3 $MM run <key> [args]`. Don't `show` or read the script first; that wastes the tokens the reflex saves.
     - `.sh`/`.py`: it executes and records the outcome itself. Work from its output only.
     - `.md`: it prints the recipe. Follow it literally, then `hit <key>` (or `fail <key>`).
     - On failure: do the task normally, then fix the reflex and re-`save` it.
   - **No reflex:** do the task normally.
2. **Log (after finishing a non-trivial task by hand):**
   `python3 $MM log <task-key> "<one-line summary>" --triggers "word,word,word"`
   - The key names the *kind* of task, not this instance: `deploy-worker`, not `deploy-worker-tuesday`. Reuse an existing key when it fits (`python3 $MM list`).
   - Triggers are words a future request would contain.
   - Add `--global` only if the task is the same in every project (e.g. "clean up Downloads").
3. **Compile automatically on the 3rd run.** When `log` prints `COMPILE NOW`, or the Stop hook says "Compile it into a reflex", write the reflex straight away; no user approval needed. Save it to the scratchpad, then `python3 $MM save <key> <file>` (same `--global` choice as the log). Compiling only writes a file. A reflex still runs only when you choose to run it.

The hooks log known tasks for you. You'll only be asked to log a brand-new task (at most once per session) or to compile one.

## Instinct (zero-token tier)
After a script reflex succeeds 3 runs in a row **with no args**, it becomes **eligible** to be an instinct. An instinct is run by the prompt hook directly, with no model call. Eligible is not promoted: **only the user promotes**, by running `mm promote <key>`, which shows them the script, then `mm promote <key> --confirm <hash>`.
- When `run` prints `ELIGIBLE` or the hook hint says the reflex is eligible, tell the user in one line that they can approve it with `mm promote <key>`. **Never run `promote` yourself.**
- When compiling, give every arg a sensible default (cwd, `REPORT.md`, `main`...): a reflex that always needs args can never become eligible.
- Approval is pinned to the script's hash. Re-saving or editing the script revokes it. A failed instinct is demoted automatically.

## Compiling a good reflex

| Task is... | Reflex form |
|---|---|
| Same commands each time, only values differ | `.sh` / `.py` that takes those values as args (`$1`, `--name`), runs `set -euo pipefail`, and prints a short success line |
| Needs judgment (reading code, choosing names) | `.md` recipe: exact files to open, exact commands, decisions pre-made, ≤15 lines |

A reflex contains: the exact commands, paths **relative to the project root** (instincts run from there; never absolute paths), the inputs as parameters with defaults, and a check that proves it worked. Bake in what earlier runs learned (gotchas, flags that were needed). Leave out explanations.

## Quick reference
| Command | Does |
|---|---|
| `match "<text>"` | find a reflex for a task |
| `run <key> [args]` | execute a reflex, auto-record outcome |
| `log <key> "<summary>" --triggers "a,b" [--global]` | count a manual run |
| `save <key> <file> [--global]` | install / replace reflex |
| `hit` / `fail <key>` | record outcome (2 fails in a row disables it) |
| `promote` / `demote <key>` | user-only: review + approve an instinct / turn it off |
| `list [--all]`, `show <key>`, `forget <key>` | inspect / remove |

## Common mistakes
- Using a different key every time, so nothing reaches the threshold. Run `list` and reuse keys.
- Exploring the codebase "just to be sure" when a reflex exists. That spends the tokens the reflex was meant to save. Trust it; the `fail` path covers drift.
- Hardcoding one run's values into a script instead of making them parameters.
- Logging trivial one-step tasks. Only log tasks that took several tool calls.
