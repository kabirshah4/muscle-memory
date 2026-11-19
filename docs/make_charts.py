#!/usr/bin/env python3
"""Render the README chart from bench/results.csv as static SVGs (light + dark).

Run: python3 docs/make_charts.py   (writes docs/cost-light.svg, docs/cost-dark.svg)
CI regenerates the chart and fails if it differs from the committed SVGs.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "bench"))
from summarize import load, summary  # noqa: E402

FONT = 'system-ui, -apple-system, "Segoe UI", Helvetica, Arial, sans-serif'
# Tier colors: categorical slots 1-3 (validated light & dark); control is the neutral baseline gray.
THEMES = {
    "light": dict(surface="#fcfcfb", ink="#0b0b0b", ink2="#52514e", muted="#898781", grid="#e1e0d9",
                  base="#c3c2b7", hand="#2a78d6", reflex="#eb6834", instinct="#1baf7a", control="#a8a7a0"),
    "dark": dict(surface="#1a1a19", ink="#f0efec", ink2="#c3c2b7", muted="#898781", grid="#2c2c2a",
                 base="#383835", hand="#3987e5", reflex="#d95926", instinct="#199e70", control="#6b6a65"),
}
SERIES = [("hand", "By hand"), ("reflex", "Reflex"), ("instinct", "Instinct"), ("control", "Control (plugin off)")]


def col(x, y_top, w, y_base, r=4):
    """Column path: 4px rounded data end on top, square at the baseline."""
    h = y_base - y_top
    r = max(0.5, min(r, h / 2, w / 2))
    return (f"M{x:.1f},{y_base:.1f} V{y_top + r:.1f} Q{x:.1f},{y_top:.1f} {x + r:.1f},{y_top:.1f} "
            f"H{x + w - r:.1f} Q{x + w:.1f},{y_top:.1f} {x + w:.1f},{y_top + r:.1f} V{y_base:.1f} Z")


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def cost_chart(t, res, meta):
    W, H = 720, 390
    L, R, T, B = 64, 696, 116, 300
    tasks = list(res)
    top = max((s["cost"] or 0) for ph in res.values() for s in ph.values() if s.get("n")) or 0.1
    ymax = max(0.1, round(top + 0.05, 1))
    y = lambda v: B - (v / ymax) * (B - T)
    band = (R - L) / len(tasks)
    bw, gap = 18, 2
    desc = "; ".join(f"{task}: " + ", ".join(
        f"{name} {'n/a' if not ph.get(k, {}).get('n') else '$%.3f' % ph[k]['cost']}" for k, name in SERIES)
        for task, ph in res.items())
    s = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" '
         f'aria-labelledby="t d" font-family=\'{FONT}\'>',
         f'<title id="t">Median cost per request, by task and tier</title><desc id="d">{esc(desc)}</desc>',
         f'<rect width="{W}" height="{H}" rx="12" fill="{t["surface"]}"/>',
         f'<text x="24" y="36" font-size="17" font-weight="600" fill="{t["ink"]}">Median cost per request</text>',
         f'<text x="24" y="58" font-size="13" fill="{t["ink2"]}">{len(tasks)} task types, real claude -p sessions '
         f'({esc(meta.get("date", ""))}, Claude Code {esc(meta.get("claude_cli", "").split(" ")[0])}). '
         f'n/a: never reached that tier.</text>']
    x = 24
    for k, name in SERIES:
        s.append(f'<rect x="{x}" y="82" width="10" height="10" rx="2" fill="{t[k]}"/>')
        s.append(f'<text x="{x + 16}" y="91" font-size="12" fill="{t["ink2"]}">{name}</text>')
        x += 16 + len(name) * 7 + 22
    step = 0.05 if ymax <= 0.3 else 0.1
    v = 0.0
    while v <= ymax + 1e-9:
        yy = y(v)
        if v:
            s.append(f'<line x1="{L}" x2="{R}" y1="{yy:.1f}" y2="{yy:.1f}" stroke="{t["grid"]}" stroke-width="1"/>')
        s.append(f'<text x="{L - 10}" y="{yy + 4:.1f}" font-size="11" text-anchor="end" fill="{t["muted"]}">'
                 f'{"$0" if not v else f"${v:.2f}"}</text>')
        v = round(v + step, 2)
    group_w = len(SERIES) * bw + (len(SERIES) - 1) * gap
    for i, task in enumerate(tasks):
        x0 = L + band * i + (band - group_w) / 2
        for j, (k, _) in enumerate(SERIES):
            st = res[task].get(k)
            bx = x0 + j * (bw + gap)
            if not st or not st.get("n"):
                s.append(f'<text x="{bx + bw / 2:.1f}" y="{B - 6}" font-size="9" text-anchor="middle" '
                         f'fill="{t["muted"]}">n/a</text>')
            elif st["cost"] > 0:
                s.append(f'<path d="{col(bx, y(st["cost"]), bw, B)}" fill="{t[k]}"/>')
            else:  # zero cost: no bar, a ringed marker on the baseline keeps the value visible
                s.append(f'<circle cx="{bx + bw / 2:.1f}" cy="{B}" r="5" fill="{t[k]}" stroke="{t["surface"]}" '
                         f'stroke-width="2"/>')
                s.append(f'<text x="{bx + bw / 2:.1f}" y="{B - 12}" font-size="11" font-weight="600" '
                         f'text-anchor="middle" fill="{t["ink"]}">$0</text>')
        s.append(f'<text x="{L + band * i + band / 2:.1f}" y="{B + 22}" font-size="13" text-anchor="middle" '
                 f'fill="{t["ink2"]}">{esc(task)}</text>')
    s.append(f'<line x1="{L}" x2="{R}" y1="{B}" y2="{B}" stroke="{t["base"]}" stroke-width="1"/>')
    s.append(f'<text x="24" y="{H - 24}" font-size="11" fill="{t["muted"]}">Data: bench/results.csv · '
             f'regenerate with python3 bench/run.py, then python3 docs/make_charts.py</text>')
    s.append("</svg>")
    return "\n".join(s)


if __name__ == "__main__":
    res = summary(load(os.path.join(REPO, "bench", "results.csv")))
    try:
        with open(os.path.join(REPO, "bench", "results.meta.json")) as f:
            meta = json.load(f)
    except (OSError, ValueError):
        meta = {}
    for mode, t in THEMES.items():
        path = os.path.join(HERE, f"cost-{mode}.svg")
        with open(path, "w") as f:
            f.write(cost_chart(t, res, meta) + "\n")
        print("wrote", path)
