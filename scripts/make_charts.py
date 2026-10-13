#!/usr/bin/env python
"""Generate the README/Pages figures as SVG, from results/ -- not by hand.

Charts are generated from the saved result files so they cannot drift from the
numbers in the report. Re-run after any new eval and the figures update.

SVG rather than PNG: crisp at any size, tiny, diffable, and no new dependency.
Each chart is emitted twice -- light and dark -- because GitHub serves READMEs
in both themes and a chart with one hard-coded surface is unreadable in the
other. The README pairs them with <picture media="prefers-color-scheme">.

Palette: the dataviz reference instance, categorical slots 1 and 2, validated
in both modes (worst adjacent CVD dE 24.7 light / 26.8 dark against an >=8
target; normal-vision 33.6 / 31.8 against a >=15 floor).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
RESULTS = ROOT / "results"
OUT = ROOT / "charts"

THEMES = {
    "light": dict(surface="#fcfcfb", ink="#0b0b0b", ink2="#52514e", ink3="#8a8982",
                  grid="#e4e3de", s1="#2a78d6", s2="#eb6834", crit="#d03b3b"),
    "dark":  dict(surface="#1a1a19", ink="#ffffff", ink2="#c3c2b7", ink3="#8a8982",
                  grid="#333331", s1="#3987e5", s2="#d95926", crit="#d03b3b"),
}

W, H = 760, 450
PAD = dict(t=92, r=28, b=108, l=58)
FONT = ("-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,"
        "Arial,sans-serif")


def esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def wrap(text: str, max_chars: int) -> list[str]:
    """Greedy word wrap. Notes overflowed the right edge before this existed --
    SVG does not wrap text, so every multi-line string has to be laid out."""
    words, lines, cur = text.split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > max_chars:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines


def note_lines(a, note: str, t: dict, color: str, bold: bool = False,
               size: float = 11.5) -> None:
    """Render a wrapped note ending flush with the bottom padding."""
    if not note:
        return
    max_chars = int((W - PAD["l"] - PAD["r"]) / (size * 0.52))
    lines = wrap(note, max_chars)
    weight = ' font-weight="600"' if bold else ""
    for i, line in enumerate(lines):
        y = H - 14 - (len(lines) - 1 - i) * (size + 3)
        a(f'<text x="{PAD["l"]}" y="{y:.1f}" font-size="{size}" '
          f'fill="{color}"{weight}>{esc(line)}</text>')


def grouped_bars(title: str, subtitle: str, groups: list[str],
                 series: list[tuple[str, list[float]]], t: dict,
                 ymax: float = 100.0, note: str = "",
                 fmt: str = "{:.1f}%") -> str:
    """Grouped vertical bars. Every bar is directly labelled, so identity never
    rests on color alone and the sub-3:1 relief rule is satisfied."""
    pw = W - PAD["l"] - PAD["r"]
    ph = H - PAD["t"] - PAD["b"]
    x0, y0 = PAD["l"], PAD["t"]
    n_g, n_s = len(groups), len(series)
    gw = pw / n_g
    bw = min(56.0, (gw - 26) / n_s)
    o: list[str] = []
    a = o.append

    a(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
      f'width="{W}" height="{H}" role="img" '
      f'aria-label="{esc(title)}. {esc(subtitle)}">')
    a(f'<rect width="{W}" height="{H}" fill="{t["surface"]}"/>')
    a(f'<g font-family="{FONT}">')
    a(f'<text x="{PAD["l"]}" y="34" font-size="19" font-weight="650" '
      f'fill="{t["ink"]}">{esc(title)}</text>')
    a(f'<text x="{PAD["l"]}" y="56" font-size="13" fill="{t["ink2"]}">'
      f'{esc(subtitle)}</text>')

    # gridlines, recessive
    for k in range(0, 6):
        v = ymax * k / 5
        y = y0 + ph - ph * (v / ymax)
        a(f'<line x1="{x0}" y1="{y:.1f}" x2="{x0+pw}" y2="{y:.1f}" '
          f'stroke="{t["grid"]}" stroke-width="1"/>')
        a(f'<text x="{x0-10}" y="{y+4:.1f}" font-size="11" text-anchor="end" '
          f'fill="{t["ink3"]}">{v:.0f}</text>')

    colors = [t["s1"], t["s2"]]
    for gi, g in enumerate(groups):
        gx = x0 + gw * gi
        span = bw * n_s + 2 * (n_s - 1)        # 2px surface gap between bars
        start = gx + (gw - span) / 2
        for si, (sname, vals) in enumerate(series):
            v = vals[gi]
            if v is None:
                continue
            bh = ph * (v / ymax)
            bx = start + si * (bw + 2)
            by = y0 + ph - bh
            a(f'<path d="M{bx:.1f},{y0+ph:.1f} V{by+4:.1f} '
              f'a4,4 0 0 1 4,-4 h{bw-8:.1f} a4,4 0 0 1 4,4 '
              f'V{y0+ph:.1f} Z" fill="{colors[si]}"/>')
            a(f'<text x="{bx+bw/2:.1f}" y="{by-7:.1f}" font-size="11.5" '
              f'font-weight="600" text-anchor="middle" fill="{t["ink"]}">'
              f'{fmt.format(v)}</text>')
        a(f'<text x="{gx+gw/2:.1f}" y="{y0+ph+20:.1f}" font-size="12" '
          f'text-anchor="middle" fill="{t["ink2"]}">{esc(g)}</text>')

    a(f'<line x1="{x0}" y1="{y0+ph}" x2="{x0+pw}" y2="{y0+ph}" '
      f'stroke="{t["ink3"]}" stroke-width="1"/>')

    # legend, always present for >=2 series
    lx = PAD["l"]
    ly = H - 36
    for si, (sname, _) in enumerate(series):
        a(f'<rect x="{lx}" y="{ly-9}" width="11" height="11" rx="2.5" '
          f'fill="{colors[si]}"/>')
        a(f'<text x="{lx+17}" y="{ly}" font-size="12" fill="{t["ink2"]}">'
          f'{esc(sname)}</text>')
        lx += 22 + len(sname) * 6.6
    note_lines(a, note, t, t["crit"], bold=True, size=11.5)
    a("</g></svg>")
    return "\n".join(o)


def simple_bars(title: str, subtitle: str, labels: list[str], vals: list[float],
                t: dict, colors: list[str], note: str = "",
                ymax: float = 100.0) -> str:
    pw = W - PAD["l"] - PAD["r"]
    ph = H - PAD["t"] - PAD["b"]
    x0, y0 = PAD["l"], PAD["t"]
    gw = pw / len(labels)
    bw = min(120.0, gw - 70)
    o: list[str] = []
    a = o.append
    a(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
      f'width="{W}" height="{H}" role="img" '
      f'aria-label="{esc(title)}. {esc(subtitle)}">')
    a(f'<rect width="{W}" height="{H}" fill="{t["surface"]}"/>')
    a(f'<g font-family="{FONT}">')
    a(f'<text x="{PAD["l"]}" y="34" font-size="19" font-weight="650" '
      f'fill="{t["ink"]}">{esc(title)}</text>')
    a(f'<text x="{PAD["l"]}" y="56" font-size="13" fill="{t["ink2"]}">'
      f'{esc(subtitle)}</text>')
    for k in range(0, 6):
        v = ymax * k / 5
        y = y0 + ph - ph * (v / ymax)
        a(f'<line x1="{x0}" y1="{y:.1f}" x2="{x0+pw}" y2="{y:.1f}" '
          f'stroke="{t["grid"]}" stroke-width="1"/>')
        a(f'<text x="{x0-10}" y="{y+4:.1f}" font-size="11" text-anchor="end" '
          f'fill="{t["ink3"]}">{v:.0f}</text>')
    for i, (lab, v) in enumerate(zip(labels, vals)):
        bh = ph * (v / ymax)
        bx = x0 + gw * i + (gw - bw) / 2
        by = y0 + ph - bh
        a(f'<path d="M{bx:.1f},{y0+ph:.1f} V{by+4:.1f} '
          f'a4,4 0 0 1 4,-4 h{bw-8:.1f} a4,4 0 0 1 4,4 V{y0+ph:.1f} Z" '
          f'fill="{colors[i]}"/>')
        a(f'<text x="{bx+bw/2:.1f}" y="{by-9:.1f}" font-size="17" '
          f'font-weight="700" text-anchor="middle" fill="{t["ink"]}">'
          f'{v:.0f}%</text>')
        for li, line in enumerate(lab.split("|")):
            a(f'<text x="{bx+bw/2:.1f}" y="{y0+ph+20+li*15:.1f}" font-size="12" '
              f'text-anchor="middle" fill="{t["ink2"]}">{esc(line)}</text>')
    a(f'<line x1="{x0}" y1="{y0+ph}" x2="{x0+pw}" y2="{y0+ph}" '
      f'stroke="{t["ink3"]}" stroke-width="1"/>')
    note_lines(a, note, t, t["ink2"], size=11.5)
    a("</g></svg>")
    return "\n".join(o)


def main() -> None:
    OUT.mkdir(exist_ok=True)
    summary = {a["arm"]: a for a in json.loads((RESULTS / "summary.json").read_text())}
    frag = json.loads((RESULTS / "fragmentation_by_tier.json").read_text())

    # Boosted/withheld split is recomputed rather than hard-coded.
    from dgeval.experiments.keyterm import false_trigger_analysis
    from dgeval.metrics.scorers import score_clip
    man = {json.loads(l)["uid"]: json.loads(l)
           for l in (ROOT / "data/corpus/manifest.jsonl").read_text().splitlines()}
    scores = {}
    for arm in ("baseline", "keyterm_all", "keyterm_half"):
        sc = []
        for line in (RESULTS / f"{arm}.jsonl").read_text().splitlines():
            r = json.loads(line)
            row = man.get(r["uid"])
            if row:
                sc.append(score_clip(row, r["hypothesis"]))
        scores[arm] = sc
    fta = false_trigger_analysis(scores)["per_arm"]

    arms = ["baseline", "keyterm_all", "keyterm_half"]
    labels = ["no keyterm list", "FULL list (all 50)", "HALF list (one per pair)"]
    boosted = [fta[a]["boosted"]["accuracy"] * 100 for a in arms]
    withheld = [fta[a]["withheld"]["accuracy"] * 100 for a in arms]
    xconf = fta["keyterm_half"]["withheld"]["cross_confusion_rate"] * 100

    conds = ["clean_16k", "opus_16k_reverb", "telephony_8k_ulaw", "telephony_noisy_10db"]
    nice = ["clean 16 kHz", "VoIP + echo", "telephone 8 kHz", "telephone + noise"]
    def by_cond(arm):
        d = {c["condition"]: c for c in summary[arm]["by_condition"]}
        return [d[c]["drug_accuracy"] * 100 for c in conds]

    fr = {(r["tier"], r["endpointing_ms"]): r for r in frag["rows"]}

    charts = {
        "keyterm-false-trigger": lambda t: grouped_bars(
            "A partial keyterm list is worse than no list at all",
            "Drug-name accuracy, by whether the spoken drug was on the boost list. "
            "n=376 per arm.",
            labels,
            [("drug WAS on the list", boosted), ("drug was NOT on the list", withheld)],
            t,
            note=(f"With the half list, {xconf:.1f}% of un-listed drugs were "
                  f"transcribed AS their listed look-alike. With no list: 0%."),
        ),
        "channel-degradation": lambda t: grouped_bars(
            "Drug names degrade with the phone channel",
            "Drug-name accuracy by channel condition. A number measured on clean "
            "audio is not a number you can launch on.",
            nice,
            [("no keyterm list", by_cond("baseline")),
             ("full keyterm list", by_cond("keyterm_all"))],
            t,
        ),
        "fragmentation": lambda t: simple_bars(
            "Real speech splits far more than synthetic speech",
            "Share of requests arriving as more than one transcript segment, at "
            "endpointing=300 ms.",
            ["synthetic voices|(what most evals use)",
             "a real human voice|(16 recordings)"],
            [fr[("A_domain_synthetic", 300)]["fragmentation_rate"] * 100,
             fr[("C_real_domain", 300)]["fragmentation_rate"] * 100],
            t, [t["s1"], t["s2"]],
            note="A split request delivers the drug name first and the "
                 "prescription number second. Acting on the first half looks "
                 "like acting on a complete request.",
        ),
    }

    for name, fn in charts.items():
        for mode, t in THEMES.items():
            p = OUT / f"{name}-{mode}.svg"
            p.write_text(fn(t))
            print(f"  {p.relative_to(ROOT)}  {p.stat().st_size//1024} KB")


if __name__ == "__main__":
    main()
