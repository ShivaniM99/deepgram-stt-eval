#!/usr/bin/env python
"""Quality-check tier C recordings. Local only -- no API key, no cost.

Checks the things that silently ruin a real-voice tier:

  SNR            speech against the clip's own noise floor. Absolute level
                 does not matter (it gets normalized); the ratio does.
  clipping       samples pinned at full scale, which no gain can undo
  head transient whether the clip opens with something LOUDER than the speech
                 that follows -- the recorder keypress. On the first pass
                 through this corpus 13 of 16 clips had one.
  label pairing  every .wav has a .txt and vice versa, because an unlabelled
                 or mislabelled clip corrupts the aggregate silently

  python scripts/qc_recordings.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np
import soundfile as sf

from dgeval.config import RECORDINGS, tier_c_media
from dgeval.corpus.prepare import head_transient_db, tail_transient_db
from rich.console import Console
from rich.table import Table

console = Console()


def snr_db(x: np.ndarray, sr: int) -> float:
    n = int(sr * 0.02)
    f = len(x) // n
    if f < 5:
        return 0.0
    d = 20 * np.log10(np.sqrt((x[:f * n].reshape(f, n) ** 2).mean(axis=1)) + 1e-12)
    noise = float(np.percentile(d, 10))
    mask = d > noise + 10
    speech = float(np.percentile(d[mask], 75)) if mask.any() else noise
    return speech - noise


def main() -> None:
    wavs = tier_c_media()
    labels = sorted(RECORDINGS.glob("c*.txt"))
    if not wavs:
        raise SystemExit(f"no recordings in {RECORDINGS}; run `make record`")

    t = Table(title="tier C quality check", show_edge=False)
    for c in ("clip", "dur", "SNR", "clip%", "head", "tail", "verdict"):
        t.add_column(c, justify="left" if c == "clip" else "right")

    problems: dict[str, list[str]] = {}
    for w in wavs:
        x, sr = sf.read(str(w), dtype="float32", always_2d=False)
        if x.ndim > 1:
            x = x.mean(axis=1)
        dur = len(x) / sr
        s = snr_db(x, sr)
        clipped = float(np.mean(np.abs(x) > 0.99)) * 100
        head = head_transient_db(w)
        tail = tail_transient_db(w)

        issues = []
        if dur < 1.5:
            issues.append("too short")
        if s < 15:
            issues.append("low SNR")
        if clipped > 0.1:
            issues.append("clipping")
        if head > 0:
            issues.append("start keypress louder than speech")
        elif head > -6:
            issues.append("start keypress audible")
        if tail > 0:
            issues.append("stop keypress louder than speech")
        if not (RECORDINGS / f"{w.stem}.txt").exists():
            issues.append("NO LABEL")
        if issues:
            problems[w.stem] = issues

        style = "red" if any(i in ("keypress louder than speech", "NO LABEL",
                                   "low SNR", "clipping") for i in issues) else \
                ("yellow" if issues else "green")
        t.add_row(w.stem, f"{dur:.1f}s", f"{s:.0f} dB", f"{clipped:.1f}%",
                  f"{head:+.1f}", f"{tail:+.1f}",
                  f"[{style}]{', '.join(issues) if issues else 'ok'}[/]")
    console.print(t)

    orphan_labels = [l.stem for l in labels if not (RECORDINGS / f"{l.stem}.wav").exists()]
    if orphan_labels:
        console.print(f"[yellow]labels with no audio:[/] {', '.join(orphan_labels)}")

    keypress = [k for k, v in problems.items()
                if any("keypress louder" in i for i in v)]
    console.print(f"\n{len(wavs)}/{len(labels)} clips recorded")
    if keypress:
        console.print(
            f"[yellow]{len(keypress)} clip(s) open with a keypress louder than the "
            f"speech:[/] {' '.join(keypress)}")
        console.print(
            "  The recorder now discards that window at capture, so re-recording "
            "fixes it:\n"
            f"    .venv/bin/python scripts/record_tier_c.py --only {' '.join(keypress)}\n"
            "  Whether it actually matters is measurable rather than assumed — "
            "`scripts/probe_tier_c.py` costs about a cent and checks whether "
            "Deepgram turns the transient into a word.")
    else:
        console.print("[green]no keypress artifacts; tier C is clean[/]")


if __name__ == "__main__":
    main()
