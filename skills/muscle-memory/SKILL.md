---
name: muscle-memory
description: Use when starting any multi-step task that feels routine or familiar (setup, deploy, scaffold, export, convert, release, cleanup, report), when a "[muscle-memory]" hint or request appears, when the user says "do the usual", "same as last time" or "again", or when a task was just finished that could recur.
---

# Muscle Memory

## Overview
Repeated tasks shouldn't be re-reasoned from scratch. The hooks count how often a task recurs; on the 2nd manual run you compile it into a **reflex** (a script, or a short recipe when the task needs judgment). After that, run the reflex instead of exploring and re-planning.

`MM=<this skill's base directory>/scripts/mm.py`. Reflexes belong to the **project** they were learned in (its git repository; worktrees share it) and only match there. `--global` on `log`/`save` makes one available everywhere.

## Flow

1. **Recall.** When a `[muscle-memory] Reflex '<key>' may match` hint appears and the user is asking for that task, run it **through mm**: `python3 $MM run <key> [args]`.
   - Never execute the reflex file directly or `show` it first. Only `run` records the outcome, and reading it spends the tokens it saves.
   - `.sh`/`.py`: work from its output. `.md`: it prints the recipe; follow it literally, then `python3 $MM hit <key>` (or `fail <key>`).
   - If it fails or the output is wrong: do the task normally, then fix the reflex and re-`save` it.
2. **New tasks are logged silently by the Stop hook.** Only if it asks you to log one (opt-in setting), and the task could recur:
   `python3 $MM log <task-key> "<one-line summary>" --triggers "word,word,word"`
   - The key names the *kind* of task (`deploy-worker`, not `deploy-worker-tuesday`). Reuse an existing key when it fits.
   - Triggers are the words a future request would use.
   - Add `--global` only if the task is identical in every project (e.g. "clean up Downloads").
   - Known tasks are counted by the hooks; you don't log them again.
3. **Compile on the 2nd run, automatically.** When `log` prints `COMPILE NOW` or the Stop hook says "Compile it into a reflex", write the reflex straight away; it needs no approval because it only writes a file. Save it to the scratchpad, then `python3 $MM save <key> <file> --summary "<what it does in general terms>"` (same `--global` choice as the log). Keep the summary free of this run's specific names or values, so it matches the next run with different ones.

## Compiling a good reflex

| Task is... | Reflex form |
|---|---|
| Same commands each time, only values differ | `.sh` / `.py` that takes those values as args with defaults, runs `set -euo pipefail` (or equivalent), and prints a short success line |
| Needs judgment (reading code, choosing names) | `.md` recipe: exact files, exact commands, decisions pre-made, ≤15 lines |

A reflex **reproduces exactly what the user asked for in the logged runs**: the same output file, sections and format. Not a broader or "improved" version. It contains:
- the exact commands
- paths **relative to the project root** (`run` and instincts both execute there; never absolute paths)
- every input as a parameter **with a default**, so the common request needs no args
- a check that proves it worked

Bake in what earlier runs learned (gotchas, flags). Leave out explanations.

## Instinct (zero-token tier), user-approved only
**Check the output once.** When a hint says the script's output hasn't been checked, compare what it produced with exactly what the user asked, not just whether it exited 0. If it matches: `python3 $MM verify <key>`. If not: `python3 $MM fail <key>`, do the task properly, and re-save a fixed script. Never verify output you didn't actually compare.

After 2 clean runs in a row through `mm run` and a verified output, a script reflex becomes **eligible**. A run counts if it had no args, or if every arg is a word copied from the user's prompt (so pass args exactly as the user wrote them). Only the user can promote it, by running `python3 $MM promote <key>` in their own terminal. It shows them the script and the exact prompts it will answer, then asks them to type yes. An approved instinct is run by the prompt hook with no model call, and only when the whole prompt matches one of those phrasings.
- When `run` prints `ELIGIBLE` or a hint says the reflex is eligible, tell the user in one line that they can approve it with that command. **Never run `promote` yourself, and never edit the store files.**
- Editing or re-saving the script revokes approval. A failed instinct is demoted automatically.

## Quick reference
| Command | Does |
|---|---|
| `run [--global] <key> [args]` | execute a reflex from the project root, record the outcome |
| `log [--global] <key> "<summary>" --triggers "a,b"` | record a manual run |
| `save [--global] <key> <file>` | install / replace a reflex |
| `hit` / `fail <key>` | record a recipe's outcome (2 fails in a row disables a reflex) |
| `list [--all]`, `match "<text>"` | inspect |
| `promote` / `demote <key>` | user-only: approve / turn off an instinct |

## Common mistakes
- Running the reflex file directly instead of `mm run`: nothing is recorded, so it can never earn trust.
- Compiling a reflex that does more or less than the logged task asked for.
- Exploring "just to be sure" when a reflex exists. Trust it; the failure path covers drift.
- Hardcoding one run's values instead of parameters with defaults.
- Using a new key for the same task, so nothing reaches the threshold.
