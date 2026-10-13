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
                  grid="#e4e3de", crit="#d03b3b"),
    "dark":  dict(surface="#1a1a19", ink="#ffffff", ink2="#c3c2b7", ink3="#8a8982",
                  grid="#333331", crit="#d03b3b"),
}

# One pair per chart rather than one pair reused everywhere: with a single pair,
# the same blue meant "on the list", "no word list" and "synthetic voice" on three
# charts of the same page, so the colour carried no stable meaning. Each pair is
# validated in both modes against the chart surface.
#
#   listed/left-out   blue + red      all checks pass both modes
#   without/with help violet + green  all checks pass both modes
#   computer/real     orange + aqua   aqua is 2.74:1 on the light surface, under
#                                     the 3:1 bar -- the relief rule applies and
#                                     is met: every bar carries a visible value
#                                     label, so nothing rests on colour alone.
PAIRS = {
    "listed":   {"light": ("#2a78d6", "#e34948"), "dark": ("#3987e5", "#e66767")},
    "help":     {"light": ("#4a3aa7", "#008300"), "dark": ("#9085e9", "#008300")},
    "voice":    {"light": ("#eb6834", "#1baf7a"), "dark": ("#d95926", "#199e70")},
}

W, H = 760, 500
# Bottom padding carries three stacked rows: two-line group labels, the
# legend, then a note that may wrap to two lines. Sized for all three.
PAD = dict(t=92, r=28, b=158, l=58)
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


def note_lines(a, note: str, t: dict, color: str, top: float,
               bold: bool = False, size: float = 11.5) -> None:
    """Render a wrapped note starting at `top`.

    Anchored to an explicit baseline rather than to the canvas bottom: when
    group labels grew to two lines, a bottom-anchored note collided with the
    legend above it.
    """
    if not note:
        return
    max_chars = int((W - PAD["l"] - PAD["r"]) / (size * 0.52))
    weight = ' font-weight="600"' if bold else ""
    for i, line in enumerate(wrap(note, max_chars)):
        a(f'<text x="{PAD["l"]}" y="{top + i * (size + 4):.1f}" '
          f'font-size="{size}" fill="{color}"{weight}>{esc(line)}</text>')


def grouped_bars(title: str, subtitle: str, groups: list[str],
                 series: list[tuple[str, list[float]]], t: dict,
                 colors: list[str], ymax: float = 100.0, note: str = "",
                 fmt: str = "{:.0f}%") -> str:
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
        for li, line in enumerate(g.split("\n")):
            a(f'<text x="{gx+gw/2:.1f}" y="{y0+ph+20+li*15:.1f}" font-size="12" '
              f'text-anchor="middle" fill="{t["ink2"]}">{esc(line)}</text>')

    a(f'<line x1="{x0}" y1="{y0+ph}" x2="{x0+pw}" y2="{y0+ph}" '
      f'stroke="{t["ink3"]}" stroke-width="1"/>')

    # legend, always present for >=2 series. Stacked below the group labels,
    # which may be two lines, with the note below that again.
    lx = PAD["l"]
    ly = y0 + ph + 62
    for si, (sname, _) in enumerate(series):
        a(f'<rect x="{lx}" y="{ly-9}" width="11" height="11" rx="2.5" '
          f'fill="{colors[si]}"/>')
        a(f'<text x="{lx+17}" y="{ly}" font-size="12" fill="{t["ink2"]}">'
          f'{esc(sname)}</text>')
        lx += 22 + len(sname) * 6.6
    note_lines(a, note, t, t["crit"], top=y0 + ph + 90, bold=True, size=11.5)
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
    note_lines(a, note, t, t["ink2"], top=y0 + ph + 72, size=11.5)
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
    nice = ["studio\nquality", "internet\ncall", "normal\nphone call",
            "noisy\nphone call"]
    def by_cond(arm):
        d = {c["condition"]: c for c in summary[arm]["by_condition"]}
        return [d[c]["drug_accuracy"] * 100 for c in conds]

    fr = {(r["tier"], r["endpointing_ms"]): r for r in frag["rows"]}

    charts = {
        "keyterm-false-trigger": lambda t: grouped_bars(
            "Listing some drug names made the unlisted ones worse",
            "How often the medicine name came out right. You can give the system a "
            "list of words to listen for.",
            ["we gave it\nno list",
             "we listed\nall 50 medicines",
             "we listed only\nhalf of them"],
            [("medicines we listed", boosted),
             ("medicines we left out", withheld)],
            t, list(PAIRS["listed"]["light" if t["surface"] == "#fcfcfb" else "dark"]),
            note=(f"With only half listed, {xconf:.1f}% of the left-out medicines came "
                  f"back as the name of a DIFFERENT real medicine, the one we had "
                  f"listed. With no list at all, that never happened once."),
        ),
        "channel-degradation": lambda t: grouped_bars(
            "The worse the phone line, the more medicine names it gets wrong",
            "How often the medicine name came out right, on four qualities of audio.",
            nice,
            [("without a word list", by_cond("baseline")),
             ("with a full word list", by_cond("keyterm_all"))],
            t, list(PAIRS["help"]["light" if t["surface"] == "#fcfcfb" else "dark"]),
            note="Testing on studio-quality audio would have made this look better "
                 "than a real phone line ever is. The full word list helps, but not "
                 "enough to reach the 98% this job needs.",
        ),
        "fragmentation": lambda t: simple_bars(
            "A real person gets cut off mid-request far more often",
            "How often one spoken request was chopped into two or more pieces, "
            "because the speaker paused.",
            ["computer voices|(what most tests use)",
             "a real person|(16 recordings)"],
            [fr[("A_domain_synthetic", 300)]["fragmentation_rate"] * 100,
             fr[("C_real_domain", 300)]["fragmentation_rate"] * 100],
            t, list(PAIRS["voice"]["light" if t["surface"] == "#fcfcfb" else "dark"]),
            note="Being cut in half means the medicine name arrives first and the "
                 "prescription number arrives separately a moment later. The first "
                 "half looks like a complete request, so it is easy to act on by "
                 "mistake.",
        ),
    }

    for name, fn in charts.items():
        for mode, t in THEMES.items():
            p = OUT / f"{name}-{mode}.svg"
            p.write_text(fn(t))
            print(f"  {p.relative_to(ROOT)}  {p.stat().st_size//1024} KB")


if __name__ == "__main__":
    main()
