#!/usr/bin/env python3
"""Render the README charts as static SVGs, one light and one dark per chart.

Run: python3 docs/make_charts.py   (writes docs/*.svg)
Data: the end-to-end test runs in README "Measured results".
"""
import os

OUT = os.path.dirname(os.path.abspath(__file__))
FONT = 'system-ui, -apple-system, "Segoe UI", Helvetica, Arial, sans-serif'

# Tier colors: categorical slots 1-3, validated light & dark (dataviz palette).
THEMES = {
    "light": dict(surface="#fcfcfb", ink="#0b0b0b", ink2="#52514e", muted="#898781",
                  grid="#e1e0d9", base="#c3c2b7", hand="#2a78d6", reflex="#eb6834", instinct="#1baf7a"),
    "dark": dict(surface="#1a1a19", ink="#f0efec", ink2="#c3c2b7", muted="#898781",
                 grid="#2c2c2a", base="#383835", hand="#3987e5", reflex="#d95926", instinct="#199e70"),
}
TIER_NAME = {"hand": "By hand", "reflex": "Reflex", "instinct": "Instinct"}

COST_RUNS = [  # (run, tier, dollars)
    (1, "hand", 0.265), (2, "hand", 0.104), (3, "hand", 0.248),
    (4, "reflex", 0.185), (5, "reflex", 0.042), (6, "reflex", 0.031),
    (7, "instinct", 0.0),
]
SPEED = [("hand", 32), ("reflex", 13), ("instinct", 3)]  # seconds, one representative run each


def col(x, y_top, w, y_base, r=4):
    """Column path: 4px rounded data end on top, square at the baseline."""
    h = y_base - y_top
    r = min(r, h / 2, w / 2)
    return (f"M{x},{y_base} V{y_top + r} Q{x},{y_top} {x + r},{y_top} H{x + w - r} "
            f"Q{x + w},{y_top} {x + w},{y_top + r} V{y_base} Z")


def bar(x_base, y, length, h, r=4):
    """Horizontal bar: square at the baseline, 4px rounded data end on the right."""
    r = min(r, length / 2, h / 2)
    xe = x_base + length
    return (f"M{x_base},{y} H{xe - r} Q{xe},{y} {xe},{y + r} V{y + h - r} "
            f"Q{xe},{y + h} {xe - r},{y + h} H{x_base} Z")


def svg_open(w, h, t, title, desc):
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
        f'role="img" aria-labelledby="t d" font-family=\'{FONT}\'>',
        f'<title id="t">{title}</title><desc id="d">{desc}</desc>',
        f'<rect width="{w}" height="{h}" rx="12" fill="{t["surface"]}"/>',
    ]


def legend(t, x, y, tiers):
    out = []
    for k in tiers:
        out.append(f'<rect x="{x}" y="{y - 9}" width="10" height="10" rx="2" fill="{t[k]}"/>')
        out.append(f'<text x="{x + 16}" y="{y}" font-size="12" fill="{t["ink2"]}">{TIER_NAME[k]}</text>')
        x += 16 + len(TIER_NAME[k]) * 7 + 20
    return out


def cost_chart(t):
    W, H = 720, 372
    L, R, T, B = 64, 696, 108, 284
    ymax = 0.30
    y = lambda v: B - (v / ymax) * (B - T)
    band = (R - L) / len(COST_RUNS)
    bw = 24
    s = svg_open(W, H, t, "Cost per request, by run",
                 "Cost of the same task across 7 fresh Claude Code sessions. Runs 1-3 by hand: $0.265, $0.104, "
                 "$0.248. Runs 4-6 reflex: $0.185, $0.042, $0.031. Run 7 instinct: $0, no model call.")
    s.append(f'<text x="{L - 40}" y="36" font-size="17" font-weight="600" fill="{t["ink"]}">Cost per request</text>')
    s.append(f'<text x="{L - 40}" y="58" font-size="13" fill="{t["ink2"]}">Same task, 7 fresh sessions. '
             f'The cost falls as the task moves up a tier.</text>')
    s += legend(t, L - 40, 82, ["hand", "reflex", "instinct"])
    for v in (0, 0.1, 0.2, 0.3):
        yy = y(v)
        if v:
            s.append(f'<line x1="{L}" x2="{R}" y1="{yy}" y2="{yy}" stroke="{t["grid"]}" stroke-width="1"/>')
        s.append(f'<text x="{L - 10}" y="{yy + 4}" font-size="11" text-anchor="end" fill="{t["muted"]}">'
                 f'{"$0" if v == 0 else f"${v:.2f}"}</text>')
    for i, (run, tier, v) in enumerate(COST_RUNS):
        cx = L + band * i + band / 2
        if v > 0:
            s.append(f'<path d="{col(cx - bw / 2, y(v), bw, B)}" fill="{t[tier]}"/>')
        else:  # zero draws no bar; a ringed marker on the baseline keeps the run visible
            s.append(f'<circle cx="{cx}" cy="{B}" r="5" fill="{t[tier]}" stroke="{t["surface"]}" stroke-width="2"/>')
        s.append(f'<text x="{cx}" y="{B + 20}" font-size="11" text-anchor="middle" fill="{t["muted"]}">Run {run}</text>')
        label = {1: "$0.265", 6: "$0.031", 7: "$0"}.get(run)
        if label:
            ly = (y(v) - 8) if v > 0 else B - 14
            s.append(f'<text x="{cx}" y="{ly}" font-size="12" font-weight="600" text-anchor="middle" '
                     f'fill="{t["ink"]}">{label}</text>')
    s.append(f'<line x1="{L}" x2="{R}" y1="{B}" y2="{B}" stroke="{t["base"]}" stroke-width="1"/>')
    # tier brackets carry identity without color
    for tier, first, last in (("hand", 0, 2), ("reflex", 3, 5), ("instinct", 6, 6)):
        x1, x2 = L + band * first + 10, L + band * (last + 1) - 10
        yb = B + 36
        s.append(f'<path d="M{x1},{yb - 4} V{yb} H{x2} V{yb - 4}" fill="none" stroke="{t["base"]}" stroke-width="1"/>')
        s.append(f'<text x="{(x1 + x2) / 2}" y="{yb + 18}" font-size="12" text-anchor="middle" '
                 f'fill="{t["ink2"]}">{TIER_NAME[tier]}</text>')
        if tier == "instinct":
            s.append(f'<text x="{(x1 + x2) / 2}" y="{yb + 34}" font-size="11" text-anchor="middle" '
                     f'fill="{t["muted"]}">no model call</text>')
    s.append("</svg>")
    return "\n".join(s)


def speed_chart(t):
    W, H = 720, 220
    L, R, T = 120, 640, 84
    row, bh = 40, 24
    xmax = 35
    x = lambda v: L + (v / xmax) * (R - L)
    s = svg_open(W, H, t, "Time to answer, by tier",
                 "Seconds until the answer: by hand 32 s, reflex 13 s, instinct 3 s.")
    s.append(f'<text x="24" y="36" font-size="17" font-weight="600" fill="{t["ink"]}">Time to answer</text>')
    s.append(f'<text x="24" y="58" font-size="13" fill="{t["ink2"]}">Seconds until the report is written, '
             f'one representative run per tier.</text>')
    s.append(f'<text x="{L}" y="{T + row * 3 + 8}" font-size="11" text-anchor="middle" fill="{t["muted"]}">0</text>')
    for v in (10, 20, 30):
        s.append(f'<line x1="{x(v)}" x2="{x(v)}" y1="{T - 8}" y2="{T + row * 3 - 8}" stroke="{t["grid"]}" stroke-width="1"/>')
        s.append(f'<text x="{x(v)}" y="{T + row * 3 + 8}" font-size="11" text-anchor="middle" fill="{t["muted"]}">{v}s</text>')
    for i, (tier, v) in enumerate(SPEED):
        yy = T + row * i
        s.append(f'<text x="{L - 12}" y="{yy + bh / 2 + 4}" font-size="13" text-anchor="end" fill="{t["ink2"]}">'
                 f'{TIER_NAME[tier]}</text>')
        s.append(f'<path d="{bar(L, yy, x(v) - L, bh)}" fill="{t[tier]}"/>')
        s.append(f'<text x="{x(v) + 8}" y="{yy + bh / 2 + 4}" font-size="12" font-weight="600" fill="{t["ink"]}">{v}s</text>')
    s.append(f'<line x1="{L}" x2="{L}" y1="{T - 8}" y2="{T + row * 3 - 8}" stroke="{t["base"]}" stroke-width="1"/>')
    s.append("</svg>")
    return "\n".join(s)


if __name__ == "__main__":
    for mode, t in THEMES.items():
        for name, fn in (("cost", cost_chart), ("speed", speed_chart)):
            path = os.path.join(OUT, f"{name}-{mode}.svg")
            with open(path, "w") as f:
                f.write(fn(t) + "\n")
            print("wrote", path)
