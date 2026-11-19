# Reference

## Other agent CLIs

The engine is one Python script, so the same reflexes work across tools and are shared between them. For CLIs other than Claude Code, install the skill and hooks with one command (it keeps your existing hooks, backs up the config file first, and `uninstall` removes only its own entries):

```bash
python3 skills/muscle-memory/scripts/mm.py install --cli codex     # or gemini, cursor
```

| CLI | Zero-token instincts and `mm <key>` | Reflex hints | Learns new tasks automatically | Verified |
|---|:---:|:---:|:---:|---|
| **Claude Code** | ✅ | ✅ | ✅ | live sessions + benchmark |
| **Codex CLI** | ✅ | ✅ | ✅ | live session (codex-cli 0.170): an approved instinct blocked the prompt and ran with no model call. Codex asks you to trust the hooks once |
| **Gemini CLI** (experimental) | ✅ | ✅ | via the skill | protocol tests only |
| **Cursor CLI** (experimental) | ✅ | ❌ (no context injection) | via the skill | protocol tests only |
| **Copilot CLI, OpenCode** | ❌ (their hooks can't stop a prompt) | ❌ | via the skill | copy `skills/muscle-memory` into their skills folder |

"Protocol tests" means the hook input and output follow each CLI's documented format and are covered by the test suite, but haven't been run in a live session of that CLI yet. Reports welcome.


## Environment variables

| Env var | Default | Meaning |
|---|---|---|
| `MUSCLE_MEMORY_THRESHOLD` | `2` | repeats before compiling |
| `MUSCLE_MEMORY_INSTINCT_AFTER` | `2` | clean runs before a reflex becomes eligible (runs with no inputs, or with inputs copied from your prompt) |
| `MUSCLE_MEMORY_MIN_CALLS` | `2` | tool calls in a turn before it counts as a task |
| `MUSCLE_MEMORY_ASK` | `0` | set to `1` to have Claude name and log new tasks itself (one extra turn, at most once per session) instead of logging them silently |
| `MUSCLE_MEMORY_DIR` | `~/.claude/muscle-memory` | where state is stored |
| `MUSCLE_MEMORY_COLOR` | `1` | set to `0` for a plain (uncolored) "Done!" line |

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
- Benchmark data: [`bench/results.csv`](../bench/results.csv), with run details in [`bench/results.meta.json`](../bench/results.meta.json) and condensed transcripts of every run in [`bench/raw/`](../bench/raw/) (tool calls truncated, not the full stream).
- Demo: recorded with [vhs](https://github.com/charmbracelet/vhs) in a real Claude Code session, with no editing.

More detail on instincts: [how-it-works.md](how-it-works.md).
