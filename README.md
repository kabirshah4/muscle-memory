# muscle-memory

**Do a task enough times and Claude stops thinking about it.**

> **Status: experimental (v0.3).** It works end to end, but it has been tested on a small number of tasks. Feedback and issues welcome.

A Claude Code plugin that watches for repeated tasks. On the 3rd repeat, Claude compiles the task into a **reflex**: a parameterized script, or a terse recipe when the task needs judgment. After that, Claude runs the reflex instead of exploring and re-deriving the steps. Once a script reflex has proven itself, it becomes an **instinct**: the hook runs it before Claude is even called, so it costs zero tokens.

```
runs 1–3:  "write the repo health report"  → Claude does it by hand; the task is counted
run 3:     threshold reached                → Claude compiles reflexes/repo-health-report.py
runs 4–6:  "repo health report again"       → Claude runs the reflex (1–2 tool calls)
run 7+:    "repo health report again"       → INSTINCT: hook runs it, 0 tokens, ~3 seconds
```

## Install

Requires Claude Code, `python3` (3.9+, standard library only) and `bash`. **macOS and Linux only.** Windows is untested and file locking is disabled there.

```
/plugin marketplace add kabirshah4/muscle-memory
/plugin install muscle-memory@muscle-memory
```

Install it as a plugin, not by copying the skill folder. The hooks do the work: without them, Claude rarely loads the skill on its own.

## How it works

| Phase | What happens | Model cost |
|---|---|---|
| **Discover** | A `Stop` hook counts the tool calls in each turn. After a 3+ call turn on a task it doesn't know, it asks Claude whether the task could recur and should be logged. It asks **at most once per session.** | one short extra turn, max once per session |
| **Count** | After a turn on a task it already knows, the `Stop` hook bumps the count itself. | none |
| **Compile** | On the 3rd run, Claude is asked to write a reflex: a `.sh`/`.py` script when the commands are the same every time (inputs become args with defaults), or a `.md` recipe of 15 lines or fewer when the task needs judgment. | one turn, once per task |
| **Recall** | A `UserPromptSubmit` hook matches your prompt against reflexes by keyword. On a match, it adds one line telling Claude to run `mm.py run <key>`. | reduced |
| **Instinct** | After 3 successful runs in a row with no args, a script reflex is promoted. On a strong match (score ≥ 0.75), the prompt hook runs it, shows you the output and **never calls the model**. | **zero** |
| **Self-heal** | A failed instinct is demoted, and Claude takes over with the error output. A reflex that fails twice in a row is disabled until it's fixed and re-saved. | normal |

### Scoping

Reflexes belong to the project they were learned in, meaning its git root, or the directory if it isn't a repo. They only match there. "Deploy the worker" in project B never runs project A's deploy script. Claude saves a reflex with `--global` only when the task is the same everywhere. Global reflexes match in every project, and project reflexes win ties.

State lives in `~/.claude/muscle-memory/`:

```
projects/<name>-<hash>/index.json, reflexes/   per project
global/index.json, reflexes/                   everywhere
```

## Measured results

**One example, not a benchmark.** These are headless `claude -p` sessions running a "write a repo health report" task on a small test repo, with the plugin installed from GitHub. Your numbers will depend on the task and your setup.

| Run | Tier | Cost |
|---|---|---|
| 1 | by hand, then asked to log it | $0.265 |
| 2 | by hand, counted silently | $0.104 |
| 3 | by hand, then compiled | $0.248 |
| 4–6 | reflex (Claude runs the script) | $0.185 → $0.042 → $0.031 |
| 7 | **instinct (hook runs it)** | **$0.000, ~3s** |

Costs vary a lot from run to run because of prompt caching (the first call in a while pays to write the cache). Rule of thumb: the more exploring a task normally takes, the more a reflex saves. Instinct is where the big difference is, because it removes the model call entirely. It only covers requests that need no task-specific inputs, like "the usual report" or "deploy staging".

## Using it

Nothing to do: work normally. To skip reflexes for one prompt, include the phrase **`no reflex`**.

To inspect or manage reflexes yourself:

```bash
alias mm='python3 "$(ls -d ~/.claude/plugins/cache/muscle-memory/muscle-memory/*/skills/muscle-memory/scripts/mm.py | tail -1)"'

mm list                  # this project + global (add --all for every project)
mm show deploy-worker    # read a reflex
mm run deploy-worker     # execute one (records success/failure)
mm demote deploy-worker  # stop it running without Claude
mm promote deploy-worker # let it run without Claude now
mm forget deploy-worker  # delete it
```

| Env var | Default | Meaning |
|---|---|---|
| `MUSCLE_MEMORY_THRESHOLD` | `3` | repeats before compiling |
| `MUSCLE_MEMORY_INSTINCT_AFTER` | `3` | clean no-arg runs before a reflex becomes an instinct |
| `MUSCLE_MEMORY_MIN_CALLS` | `3` | tool calls in a turn before it counts as a task |
| `MUSCLE_MEMORY_ASK` | `1` | set to `0` to never be asked about new tasks (only known tasks are counted) |
| `MUSCLE_MEMORY_DIR` | `~/.claude/muscle-memory` | where state is stored |

## Safety

Reflexes are scripts that Claude writes and runs on your machine. They stay local and are never uploaded. Instincts run with no model in the loop, so they need 3 clean runs, a strong keyword match, and the right project, and they're demoted on the first failure. Review yours with `mm list` / `mm show` now and then. Keyword matching is simple: a prompt that shares words with a reflex's triggers but means something else could match it. Use `no reflex` or `mm demote` when that happens.

## Limitations

- Matching is keyword overlap, not semantic. Requests phrased with entirely different words won't find their reflex.
- Requests with inputs ("deploy *this branch* to *prod*") stay at the reflex tier, because something has to work out the arguments.
- Tested on macOS with a handful of tasks. Expect rough edges.

## Development

```bash
python3 -m unittest discover -v tests
```

## License

MIT
