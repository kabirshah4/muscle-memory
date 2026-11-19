# Changelog

## 0.6.0 — 2026-11-19
- Output check: a script's result is compared mechanically with the by-hand runs (files, tags, content words) and only matching runs count toward instinct; `mm verify` remains for tasks that change no files
- Hit rate: `mm list` shows free runs vs near-misses for each instinct
- Codex: failed or denied tool calls no longer count
- Instinct details moved to docs/how-it-works.md
- Failed or denied tool calls no longer count toward learning a task
- Security policy, code of conduct, PR template, sources in the README

## 0.5.0 — 2026-11-19
- Instincts with inputs: learned, user-approved prompt patterns like `add a {1} module with a test`
- `mm <key> [args]` runs a saved script with no model call
- New tasks are learned silently; scripts are compiled after 2 runs
- Support for Codex, Gemini and Cursor CLIs (`mm.py install --cli ...`)
- Green "Done!" message for zero-token runs
- Reproducible benchmark in `bench/`

## 0.4.0
- Instincts are opt-in, approved in your terminal and pinned to the script's hash

## 0.3.0
- First public-ready version: reflexes, the Stop hook, project scoping
