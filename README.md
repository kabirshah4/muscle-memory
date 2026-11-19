<div align="center">

# 🧠 muscle-memory

**Do a task enough times and Claude stops thinking about it.**

A Claude Code plugin that turns your repeated tasks into reflexes,<br>
and your proven reflexes into instincts that run with **zero tokens**.

[![test](https://github.com/kabirshah4/muscle-memory/actions/workflows/test.yml/badge.svg)](https://github.com/kabirshah4/muscle-memory/actions/workflows/test.yml)
![version](https://img.shields.io/badge/version-0.5.0-2a78d6)
![status](https://img.shields.io/badge/status-experimental-eda100)
![platform](https://img.shields.io/badge/platform-macOS%20%7C%20Linux-52514e)
![license](https://img.shields.io/badge/license-MIT-1baf7a)

[Install](#install) · [How it works](#how-it-works) · [Results](#measured-results) · [Commands](#commands) · [Safety](#safety)

</div>

<br>

<p align="center">
  <img alt="A real Claude Code session. 'update the health report' and 'add a payments module with a test' are each answered in 0.1 seconds by muscle-memory with no model call: the report is updated, and the module and its passing test are created." src="docs/demo.gif" width="100%">
  <br><sub>A real Claude Code session on Opus. Both requests were learned earlier and are now done instantly, for 0 tokens. The second one takes an input (<code>payments</code>).</sub>
</p>

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/cost-dark.svg">
  <img alt="Median cost per request for 4 task types: by hand about $0.006, as a reflex $0.001 to $0.006, as an instinct $0 for all 4 tasks, and $0.005 to $0.007 with the plugin off." src="docs/cost-light.svg" width="100%">
</picture>

## Why

Ask Claude to do the same thing three times and it figures it out from scratch three times: it reads the same config, works out the same flags, and runs the same commands. muscle-memory notices the repetition, saves what Claude worked out as a script, and uses that script from then on. Once the script has proven itself, Claude isn't called at all.

| | By hand | Reflex | Instinct |
|---|:---:|:---:|:---:|
| **Who does it** | Claude, from scratch | Claude runs a saved script | The hook runs it before Claude is called |
| **Tool calls** | 2–7 | 1–3 | **0** |
| **Unlocks after** | — | 2 runs (automatic) | 2 clean runs + **your approval** |

> [!NOTE]
> **Status: experimental (v0.5).** It works end to end, and the latest benchmark shows all 4 tasks reaching zero-token runs (see [Results](#measured-results)). It has only been tested on a small number of tasks. Issues and feedback welcome.

## Install

```
/plugin marketplace add kabirshah4/muscle-memory
/plugin install muscle-memory@muscle-memory
```

Requires Claude Code, `python3` 3.10+ (standard library only) and `bash`, on **macOS or Linux**. Windows is untested.

> [!IMPORTANT]
> Install it as a **plugin**, not by copying the skill folder. The hooks do the work: without them, Claude rarely loads the skill on its own.

Then just work normally. There's nothing to configure.

### Other agent CLIs

The engine is one Python script, so the same reflexes work across tools and are shared between them. For CLIs other than Claude Code, install the skill and hooks with one command (it keeps your existing hooks, backs up the config file first, and `uninstall` removes only its own entries):

```bash
python3 skills/muscle-memory/scripts/mm.py install --cli codex     # or gemini, cursor
```

| CLI | Zero-token instincts and `mm <key>` | Reflex hints | Learns new tasks automatically | Verified |
|---|:---:|:---:|:---:|---|
| **Claude Code** | ✅ | ✅ | ✅ | live sessions + benchmark |
| **Codex CLI** | ✅ | ✅ | ✅ | protocol tests; same hook format as Claude Code. Codex asks you to trust the hooks once |
| **Gemini CLI** | ✅ | ✅ | via the skill | protocol tests only |
| **Cursor CLI** | ✅ | ❌ (no context injection) | via the skill | protocol tests only |
| **Copilot CLI, OpenCode** | ❌ (their hooks can't stop a prompt) | ❌ | via the skill | copy `skills/muscle-memory` into their skills folder |

"Protocol tests" means the hook input and output follow each CLI's documented format and are covered by the test suite, but haven't been run in a live session of that CLI yet. Reports welcome.

## How it works

```mermaid
flowchart LR
    A([You ask for a task]) --> B{Known reflex<br>for this project?}
    B -- no --> C[Claude does it by hand]
    C --> D[Stop hook counts it]
    D -- 2nd time --> E[Claude compiles a reflex<br>script or recipe]
    B -- yes, reflex --> F[Claude runs it<br>1–3 tool calls]
    F -- 2 clean runs --> G[Eligible: you review<br>and approve the script]
    B -- yes, instinct --> H[Hook runs it<br>⚡ 0 tokens]
    H -. fails .-> I[Demoted:<br>Claude takes over]
```

| Phase | What happens | Model cost |
|---|---|---|
| **Discover** | A `Stop` hook counts the successful tool calls in each turn (failed or denied ones don't count). After a 3+ call turn on a task it doesn't know, it records the task from your prompt's own words, **without asking Claude**. One-off tasks are hidden from `mm list` until they repeat. | none |
| **Count** | After a turn on a task it already knows, the hook bumps the count itself. | none |
| **Compile** | On the 2nd run, Claude compiles it **automatically, with no approval needed** (it only writes a file): a `.sh`/`.py` script when the commands are the same every time (inputs become args with defaults), or a `.md` recipe of 15 lines or fewer when the task needs judgment. | one turn, once per task |
| **Recall** | A `UserPromptSubmit` hook matches your prompt against reflexes by keyword and adds one line telling Claude to `run` the matching reflex. | reduced |
| **Instinct** | After 2 successful runs in a row, a script reflex becomes **eligible**. A run can have no inputs, or inputs that appear word for word in your prompt: "deploy the worker to staging" run with `staging` teaches the pattern `deploy the worker to {1}`. **Only you can promote it**, in your own terminal: `mm promote <key>` shows the script, its hash and the prompt wordings and patterns it will answer to, then asks you to type `yes`. When a reflex becomes eligible, the hook tells **you** directly; that message never reaches Claude, so it costs no tokens. From then on, a prompt with the same words as an approved wording, or one that follows an approved pattern word for word, runs it with **no model call**. The slot words become the script's arguments. Editing the script revokes approval. | **zero** |
| **Self-heal** | A failed instinct is demoted, and Claude takes over with the error. A reflex that fails twice in a row is disabled until it's fixed. | normal |

### Scoping

Reflexes belong to the project they were learned in (its git root) and only match there. "Deploy the worker" in project B never runs project A's deploy script. Claude saves a reflex with `--global` only when the task is the same everywhere. Project reflexes win ties.

<details>
<summary>Where state lives</summary>

```
~/.claude/muscle-memory/
├── projects/<name>-<hash>/
│   ├── index.json        counts, triggers, use/fail stats
│   └── reflexes/         <key>.sh · <key>.py · <key>.md
└── global/
    ├── index.json
    └── reflexes/
```

</details>

## Measured results

`bench/run.py` drives real headless `claude -p` sessions through 4 task types on fixture repos: 3 runs by hand, 3 runs as a reflex, an instinct run if the reflex became eligible, and 2 control runs with the plugin off. Condensed transcripts of every run (each tool call truncated to 400 characters, assistant text, and the final result with token usage) are in `bench/raw/`.

> [!NOTE]
> **Latest run:** Haiku sessions (`--model claude-haiku-5-5`), 2026-11-19, 4 task types, about $0.20 in total. **All 4 tasks reached instinct: answered in ~3s for $0.000, with correct output.** Small numbers and a cheap model, so treat this as evidence the mechanism works, not as a precise saving.

| Task | By hand | Reflex | Instinct | Control (plugin off) | Reflex used |
|---|---:|---:|---:|---:|:---:|
| report | $0.006 | $0.005 | **$0.000** | $0.007 | 2/3 |
| changelog | $0.007 | $0.002 | **$0.000** | $0.005 | 3/3 |
| csv | $0.007 | $0.001 | **$0.000** | $0.005 | 3/3 |
| scaffold | $0.006 | $0.005 | **$0.000** | $0.007 | 2/3 |

Medians; every run produced correct output. `scaffold` takes an input (the module name), so its instinct runs from a learned pattern: "scaffold a {1} module with a test". It first missed because its saved summary named one specific module; scripts are now saved with a generic summary, and the `scaffold` rows are from a re-run after that fix. Earlier runs on the default model (Opus) cost 20–30× more per request, so the same zero-call instinct saves proportionally more there. Reproduce with `python3 bench/run.py [--model ...]`, then `python3 bench/summarize.py`.

## Commands

**Zero-token shortcut:** type `mm <key> [args]` as your whole prompt (for example `mm deploy-worker staging`). The hook runs that script reflex and shows you the output. Claude is never called, and since you named the reflex yourself, it needs no approval or matching. Any other prompt that starts with "mm" goes to Claude as normal.

To skip reflexes for one prompt, include the phrase **`no reflex`**. To manage them yourself:

```bash
alias mm='python3 "$(ls -d ~/.claude/plugins/cache/muscle-memory/muscle-memory/*/skills/muscle-memory/scripts/mm.py | sort -V | tail -1)"'
```

| Command | Does |
|---|---|
| `mm list` | tasks in this project + global (`--all` for every project) |
| `mm show <key>` | print a reflex |
| `mm run <key> [args]` | execute a reflex and record success/failure |
| `mm promote <key>` | review the script and its wordings, then type `yes` (terminal only) |
| `mm demote <key>` | stop it running without Claude |
| `mm forget <key>` | delete it |

<details>
<summary>Environment variables</summary>

| Env var | Default | Meaning |
|---|---|---|
| `MUSCLE_MEMORY_THRESHOLD` | `2` | repeats before compiling |
| `MUSCLE_MEMORY_INSTINCT_AFTER` | `2` | clean runs before a reflex becomes eligible (runs with no inputs, or with inputs copied from your prompt) |
| `MUSCLE_MEMORY_MIN_CALLS` | `2` | tool calls in a turn before it counts as a task |
| `MUSCLE_MEMORY_ASK` | `0` | set to `1` to have Claude name and log new tasks itself (one extra turn, at most once per session) instead of logging them silently |
| `MUSCLE_MEMORY_DIR` | `~/.claude/muscle-memory` | where state is stored |
| `MUSCLE_MEMORY_COLOR` | `1` | set to `0` for a plain (uncolored) "Done!" line |

</details>

## Safety

Reflexes are scripts that Claude writes and runs on your machine. They stay local and are never uploaded. Because instincts run with no model in the loop, they need:

- ✅ 2 clean runs as a reflex first
- ✅ **your explicit approval** in an interactive terminal after seeing the script (`mm promote <key>`), pinned to its sha256 hash. The hash covers that one file only, not other scripts it calls.
- ✅ an exact match: the prompt must use the same words, in the same order, as a wording you approved (filler words like "the" are ignored), be 12 words or fewer, and contain no question mark or negation ("don't", "not", ...). Pattern slots only accept a single plain word (letters, digits and `. _ / : @ + = -`, no `..`), passed as an argument and never through a shell
- ✅ the right project (or an explicit `--global`)

They're demoted on the first failure. Review yours with `mm list` and `mm show` now and then.

> [!WARNING]
> Matching is keyword overlap, not meaning. For reflex hints, a prompt that shares words with a reflex's triggers but means something else could match it (Claude still decides). Instincts only fire on approved wordings. Use `no reflex` or `mm demote <key>` when that happens.

## Limitations

- Requests phrased with entirely different words won't find their reflex.
- Requests with inputs only skip Claude when they follow a learned pattern word for word, and each input is a single plain word. Anything else ("deploy *the branch I was just on*") goes to Claude.
- Tested on macOS with a handful of tasks. Expect rough edges.

## Development

```bash
python3 -m unittest discover -v tests   # 57 end-to-end tests
python3 docs/make_charts.py             # regenerate the README chart from bench/results.csv
```

## Sources

**How it works and what it saves**
- Claude Code hooks reference: see its sections on the `UserPromptSubmit` event (`decision: "block"` stops the prompt before the model, `hookSpecificOutput.additionalContext` adds context), the common `systemMessage` output, and the `Stop` event (`decision: "block"` continues the turn). [code.claude.com/docs/en/hooks](https://code.claude.com/docs/en/hooks)
- Claude Code plugins and skills: how the plugin and skill are packaged. [Plugins](https://code.claude.com/docs/en/plugins) · [Skills](https://code.claude.com/docs/en/skills)
- What drives cost per request: context re-sent on every request, cache reads and writes, output tokens. [Claude Code costs](https://code.claude.com/docs/en/costs) · [Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching) · [Pricing](https://platform.claude.com/docs/en/about-claude/pricing)

**Other CLIs** (the adapters follow these documented formats)
- Codex CLI: [hooks](https://developers.openai.com/codex/hooks/) · [skills](https://developers.openai.com/codex/skills/)
- Gemini CLI: [hooks reference](https://github.com/google-gemini/gemini-cli/blob/main/docs/hooks/reference.md)
- Cursor: [hooks](https://cursor.com/docs/hooks)
- GitHub Copilot CLI: [hooks reference](https://docs.github.com/en/copilot/reference/hooks-reference). Its prompt hook can't block, which is why it's skill-only here.
- OpenCode: [plugins](https://opencode.ai/docs/build/plugins/server). In-process TypeScript hooks, not command hooks.

**Evidence in this repo**
- Benchmark data: [`bench/results.csv`](bench/results.csv), with run details in [`bench/results.meta.json`](bench/results.meta.json) and condensed transcripts of every run in [`bench/raw/`](bench/raw/) (tool calls truncated, not the full stream).
- Demo: recorded with [vhs](https://github.com/charmbracelet/vhs) in a real Claude Code session, with no editing.

## License

MIT
