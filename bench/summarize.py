#!/usr/bin/env python3
"""Summarize bench/results.csv as markdown tables (median per task and phase).

Usage: python3 bench/summarize.py [bench/results.csv]
"""
import csv
import os
import statistics
import sys

PHASES = ["hand", "reflex", "instinct", "control"]


def load(path):
    with open(path) as f:
        return [r for r in csv.DictReader(f)]


def med(rows, field):
    vals = [float(r[field]) for r in rows if r.get(field) not in ("", None)]
    return statistics.median(vals) if vals else None


def summary(rows):
    """{task: {phase: {cost, calls, secs, ok, n}}} using medians; ok is 'passed/total'."""
    out = {}
    for r in rows:
        out.setdefault(r["task"], {}).setdefault(r["phase"], []).append(r)
    res = {}
    for task, phases in out.items():
        res[task] = {}
        for ph, rs in phases.items():
            ran = [r for r in rs if r["ok"] != ""]
            res[task][ph] = dict(
                cost=med(ran, "cost_usd"), calls=med(ran, "tool_calls"), secs=med(ran, "duration_s"),
                ok=f"{sum(r['ok'] == 'True' for r in ran)}/{len(ran)}", n=len(ran),
                used=f"{sum(r.get('used_reflex') == 'True' for r in ran)}/{len(ran)}",
                note=next((r["note"] for r in rs if r["note"]), ""))
    return res


def fmt(v, kind):
    if v is None:
        return "—"
    return f"${v:.3f}" if kind == "cost" else (f"{v:.0f}s" if kind == "secs" else f"{v:g}")


def markdown(res):
    lines = ["| Task | " + " | ".join(p.capitalize() for p in PHASES) + " | Reflex vs control |",
             "|---|" + "---:|" * len(PHASES) + "---:|"]
    for task, ph in res.items():
        cells = []
        for p in PHASES:
            s = ph.get(p)
            if not s or not s["n"]:
                cells.append("n/a" if p == "instinct" else "—")
            else:
                used = f" · used {s['used']}" if p in ("reflex", "instinct") else ""
                cells.append(f"{fmt(s['cost'], 'cost')} · {fmt(s['secs'], 'secs')} · ✓{s['ok']}{used}")
        r, c = ph.get("reflex", {}).get("cost"), ph.get("control", {}).get("cost")
        delta = f"{(1 - r / c) * 100:+.0f}% cheaper" if r is not None and c else "—"
        if r is not None and c and r > c:
            delta = f"{(r / c - 1) * 100:.0f}% dearer"
        lines.append(f"| {task} | " + " | ".join(cells) + f" | {delta} |")
    lines.append("")
    lines.append("Cells: median cost · median time · runs with correct output · runs where a reflex or instinct "
                 "was actually used. Control = same prompt as reflex runs, plugin off.")
    return "\n".join(lines)


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "results.csv")
    print(markdown(summary(load(path))))
