# muscle-memory

**Do a task enough times and Claude stops thinking about it.**

A Claude Code skill that watches for repeated tasks. On the 3rd repeat, Claude compiles the task into a **reflex**: a parameterized script, or a terse recipe when the task needs judgment. The next time you ask for it, Claude runs the reflex instead of exploring, planning and re-deriving the same steps, so it spends far fewer tokens and tool calls.

```
1st time:  "deploy the worker"  → explore, read config, figure out flags, deploy   (logged 1/3)
2nd time:  "deploy the worker"  → same thing again                                 (logged 2/3)
3rd time:  "deploy the worker"  → same thing, then COMPILE → reflexes/deploy-worker.sh
4th time+: "deploy the worker"  → hook spots the match → runs deploy-worker.sh → done
```

## Install

**As a plugin (recommended, includes the auto-match hook):**

```
/plugin marketplace add kabirshah4/muscle-memory
/plugin install muscle-memory@muscle-memory
```

**As a bare skill (no hook, Claude has to decide to load the skill):**

```bash
git clone https://github.com/kabirshah4/muscle-memory
cp -r muscle-memory/skills/muscle-memory ~/.claude/skills/
```

Requires `python3` (standard library only, no dependencies).

## How it works

| Phase | What happens |
|---|---|
| **Recall** | A `UserPromptSubmit` hook matches your prompt against saved reflexes by keyword. It runs locally and costs zero tokens. On a match it injects one line telling Claude which reflex to use. |
| **Log** | After Claude finishes a multi-step task by hand, it records it under a reusable key like `deploy-worker`, with trigger words. |
| **Compile** | When a key reaches the threshold (default 3), Claude writes a reflex: `.sh`/`.py` when the commands are the same every time (values become args), or `.md` (a ≤15-line recipe) when the task needs judgment. |
| **Self-heal** | A reflex that fails twice in a row is disabled (renamed `.broken`), and Claude falls back to doing the task normally until it's fixed and re-saved. |

State lives in `~/.claude/muscle-memory/`:

```
index.json            counts, triggers, use/fail stats
reflexes/<key>.sh     deterministic reflexes
reflexes/<key>.md     judgment reflexes
```

## CLI

You can manage reflexes yourself:

```bash
MM=~/.claude/skills/muscle-memory/scripts/mm.py   # or the plugin's path
python3 $MM list                 # everything learned so far
python3 $MM show deploy-worker   # read a reflex
python3 $MM forget deploy-worker # delete it
python3 $MM match "deploy my worker"
```

| Env var | Default | Meaning |
|---|---|---|
| `MUSCLE_MEMORY_THRESHOLD` | `3` | repeats before compiling |
| `MUSCLE_MEMORY_DIR` | `~/.claude/muscle-memory` | where state is stored |

## Safety

Reflexes are scripts that Claude writes and runs on your machine. They stay local and are never uploaded. Review them with `mm.py list` / `show` now and then, especially before sharing your reflexes folder.

## License

MIT
