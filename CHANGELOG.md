# Changelog

## Unreleased
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
