# Contributing

Issues and pull requests are welcome. The most useful reports are real tasks where muscle-memory did the wrong thing: a reflex that matched a prompt it shouldn't have, a task that never got learned, or an instinct that didn't fire.

- `python3 -m unittest discover -v tests` must pass (standard library only, Python 3.10+).
- If you change anything that affects cost, say how you measured it. `python3 bench/run.py --model claude-haiku-5-5` is the cheapest full run (about $0.20).
- Safety rules are not negotiable: nothing may run without a model call unless the user approved that exact script in their own terminal, or named it themselves with `mm <key>`.
- Charts are generated: run `python3 docs/make_charts.py` after changing `bench/results.csv`. CI fails if they're out of date.
