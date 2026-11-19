---
name: muscle-memory
description: Use when starting any multi-step task that feels routine or familiar (setup, deploy, scaffold, export, convert, release, cleanup, report), when a "[muscle-memory] Reflex ... matches" hint appears, when the user says "do the usual", "same as last time" or "again", or when a task was just finished that could recur.
---

# Muscle Memory

## Overview
Repeated tasks shouldn't be re-reasoned from scratch. Track how often a task recurs; after 3 runs, compile it into a **reflex** (a parameterized script or a terse recipe). After that, recall the reflex and execute it with no exploring or re-planning.

`MM=<this skill's base directory>/scripts/mm.py` (state lives in `~/.claude/muscle-memory/`)

## Flow

1. **Recall (before doing anything else):** `python3 $MM match "<one-line task description>"`
   - **REFLEX found:** `python3 $MM show <key>`, then execute it directly.
     - `.sh`/`.py`: run it with the task's arguments. Read only its output.
     - `.md`: follow the steps literally. Skip any exploration the recipe doesn't call for.
     - Success → `python3 $MM hit <key>`. Failure → `python3 $MM fail <key>`, do the task normally, then fix the reflex and re-`save` it.
   - **No reflex:** do the task normally.
2. **Log (after finishing a non-trivial task by hand):**
   `python3 $MM log <task-key> "<one-line summary>" --triggers "word,word,word"`
   - The key names the *kind* of task, not this instance: `deploy-worker`, not `deploy-worker-tuesday`. Reuse an existing key when it fits (`python3 $MM list`).
   - Triggers are words a future request would contain.
3. **Compile (when log prints `COMPILE NOW`):** write the reflex to the scratchpad, then `python3 $MM save <key> <file>`.

## Compiling a good reflex

| Task is... | Reflex form |
|---|---|
| Same commands each time, only values differ | `.sh` / `.py` that takes those values as args (`$1`, `--name`), runs `set -euo pipefail`, and prints a short success line |
| Needs judgment (reading code, choosing names) | `.md` recipe: exact files to open, exact commands, decisions pre-made, ≤15 lines |

A reflex contains: the exact paths, the exact commands, the inputs as parameters, and a check that proves it worked. Bake in what earlier runs learned (gotchas, flags that were needed). Leave out explanations.

## Quick reference
| Command | Does |
|---|---|
| `match "<text>"` | find a reflex for a task |
| `log <key> "<summary>" --triggers "a,b"` | count a manual run |
| `save <key> <file>` | install / replace reflex |
| `hit` / `fail <key>` | record outcome (2 fails in a row disables it) |
| `list`, `show <key>`, `forget <key>` | inspect / remove |

## Common mistakes
- Using a different key every time, so nothing reaches the threshold. Run `list` and reuse keys.
- Exploring the codebase "just to be sure" when a reflex exists. That spends the tokens the reflex was meant to save. Trust it; the `fail` path covers drift.
- Hardcoding one run's values into a script instead of making them parameters.
- Logging trivial one-step tasks. Only log tasks that took several tool calls.
