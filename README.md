# muscle-memory

**Do a task enough times and Claude stops thinking about it.**

A Claude Code skill that watches for repeated tasks. On the 3rd repeat, Claude compiles the task into a **reflex**: a parameterized script, or a terse recipe when the task needs judgment. The next time you ask for it, Claude runs the reflex instead of exploring, planning and re-deriving the same steps, so it spends far fewer tokens and tool calls.

```
1st time:  "deploy the worker"  → explore, read config, figure out flags, deploy   (logged 1/3)
2nd time:  "deploy the worker"  → same thing again                                 (logged 2/3)
3rd time:  "deploy the worker"  → same thing, then COMPILE → reflexes/deploy-worker.sh
4th time+: "deploy the worker"  → hook spots the match → mm.py run deploy-worker → done
```

## Install

**As a plugin (required for it to work automatically):**

```
/plugin marketplace add kabirshah4/muscle-memory
/plugin install muscle-memory@muscle-memory
```

**As a bare skill (not recommended):** without the plugin's hooks, Claude rarely loads the skill on its own, so it won't learn or recall reliably. Only useful if you invoke `/muscle-memory` yourself.

```bash
git clone https://github.com/kabirshah4/muscle-memory
cp -r muscle-memory/skills/muscle-memory ~/.claude/skills/
```

Requires `python3` (standard library only, no dependencies).

## How it works

| Phase | What happens |
|---|---|
| **Recall** | A `UserPromptSubmit` hook matches your prompt against saved reflexes by keyword. It runs locally and costs zero tokens. On a match it injects one line telling Claude which reflex to use. |
| **Log** | A `Stop` hook counts the tool calls in each turn. After a turn with 3+ calls that wasn't logged, it asks Claude once whether the task could recur and, if so, to log it under a reusable key like `deploy-worker`. Small turns cost nothing. |
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
python3 $MM run deploy-worker prod  # execute a reflex, records hit/fail
```

| Env var | Default | Meaning |
|---|---|---|
| `MUSCLE_MEMORY_THRESHOLD` | `3` | repeats before compiling |
| `MUSCLE_MEMORY_DIR` | `~/.claude/muscle-memory` | where state is stored |
| `MUSCLE_MEMORY_MIN_CALLS` | `3` | tool calls in a turn before the Stop hook suggests logging |

## Does it save tokens?

From an end-to-end test with headless `claude -p` sessions running a "write a repo health report" task:

- Logged on runs 1–3, compiled into a `.sh` reflex on run 3, reused on a reworded 4th request.
- Reflex runs used **10–72% fewer output tokens** (657 vs 2,370 in the best pair) and finished up to **2.5× faster** (13s vs 32s).
- Total cost savings ranged from **~35% to ~5%**. Claude Code's fixed context (system prompt, tools) is the same either way. Reflexes only cut the reasoning and exploration on top of it.

Rule of thumb: the more exploring a task normally takes (reading config, figuring out flags, trial and error), the more a reflex saves. Tasks Claude already does in 2 tool calls won't get much cheaper.

## Safety

Reflexes are scripts that Claude writes and runs on your machine. They stay local and are never uploaded. Review them with `mm.py list` / `show` now and then, especially before sharing your reflexes folder.

## License

MIT
