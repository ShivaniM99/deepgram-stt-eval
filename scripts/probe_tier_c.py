#!/usr/bin/env python
"""Does the recorder keypress actually become a word? ~1 cent to find out.

Transcribes the 16 tier C clips at clean quality (about 1.5 minutes of audio,
roughly $0.007) and checks whether Deepgram emits anything BEFORE the first
real word of the reference. That is the only question that decides whether
20 minutes of re-recording is warranted.

The alternative is guessing. A leading transient might be ignored by the
recognizer entirely, or it might become a spurious word -- which would inflate
tier C WER by roughly a word per clip and make real speech look harder than it
is, corrupting the single most important comparison in the project. One cent
answers it; intuition does not.

  python scripts/probe_tier_c.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dgeval.asr.batch import AsrConfig, transcribe_one
from dgeval.config import MANIFEST, BudgetGuard, has_api_key
from dgeval.metrics.normalize import normalize
from rich.console import Console
from rich.table import Table

console = Console()


def main() -> None:
    if not has_api_key():
        raise SystemExit("DEEPGRAM_API_KEY not set; see .env.example")

    rows = [json.loads(l) for l in MANIFEST.read_text().splitlines() if l.strip()]
    rows = [r for r in rows
            if r["tier"] == "C_real_domain" and r["condition"] == "clean_16k"]
    if not rows:
        raise SystemExit("no tier C clips in the manifest; run `make corpus`")

    mins = sum(r["duration_s"] for r in rows) / 60
    console.print(f"{len(rows)} clips, {mins:.1f} min, "
                  f"about ${mins * 0.0043:.4f}\n")

    guard = BudgetGuard.from_env()
    cfg = AsrConfig()

    t = Table(title="leading-insertion check", show_edge=False)
    for c in ("clip", "expected first words", "transcribed first words", "leading junk"):
        t.add_column(c, justify="left")

    n_bad = 0
    for r in sorted(rows, key=lambda r: r["uid"]):
        res = transcribe_one(Path(r["path"]), cfg, guard, r["duration_s"])
        ref = normalize(r["text"]).split()
        hyp = normalize(res.transcript).split()
        if not hyp:
            t.add_row(r["uid"], " ".join(ref[:4]), "[red](empty)[/]", "—")
            continue
        # How many hypothesis words appear before the reference's first word?
        lead = 0
        if ref:
            try:
                lead = hyp.index(ref[0])
            except ValueError:
                lead = 0
        junk = " ".join(hyp[:lead]) if lead else ""
        if junk:
            n_bad += 1
        t.add_row(r["uid"], " ".join(ref[:4]), " ".join(hyp[:4]),
                  f"[red]{junk}[/]" if junk else "[green]none[/]")
    console.print(t)

    console.print(f"\nbillable: {guard.spent_minutes:.2f} min "
                  f"= ${guard.estimated_usd():.4f}")
    if n_bad:
        console.print(
            f"\n[yellow]{n_bad}/{len(rows)} clips gained a spurious leading word.[/]\n"
            "  That inflates tier C WER and would make real speech look harder "
            "than it is. Re-record with the fixed recorder:\n"
            "    .venv/bin/python scripts/record_tier_c.py --force")
    else:
        console.print(
            "\n[green]no leading insertions — the keypress is not becoming a word.[/]\n"
            "  Keep the recordings as they are; re-recording would buy nothing.")


if __name__ == "__main__":
    main()
