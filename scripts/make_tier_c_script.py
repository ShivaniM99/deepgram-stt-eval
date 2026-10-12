#!/usr/bin/env python
"""Generate the tier C recording script and pre-write every .txt label.

Lines are chosen adversarially rather than representatively: both halves of a
confusable drug pair appear, every line carries at least one entity, and the
Rx/DOB lines use digit strings that are easy to mis-hear. The goal of tier C is
not coverage -- tier A has coverage -- it is to put a real human voice on the
cases most likely to break.

  python scripts/make_tier_c_script.py          # writes data/recordings/*.txt
  python scripts/make_tier_c_script.py --print  # just show the lines
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dgeval.config import DATA, RECORDINGS
from dgeval.corpus.generate import digits_spoken, dob_spoken
from datetime import date

# Pairs where BOTH members are recorded, so a substitution can be attributed.
PAIRS = [
    ("hydroxyzine", "hydralazine"),
    ("clonazepam", "clonidine"),
    ("prednisone", "prednisolone"),
    ("metformin", "metronidazole"),
    ("tramadol", "trazodone"),
    ("bupropion", "buspirone"),
    ("amlodipine", "amiodarone"),
    ("glipizide", "glyburide"),
]

TEMPLATES = [
    "Hi, I need to refill my prescription for {drug}.",
    "I'd like a refill on {drug}, prescription number {rx}.",
    "This is a refill request for {drug}. My date of birth is {dob}.",
    "Can you check if my {drug} is ready? The Rx number is {rx}.",
]


def build(seed: int = 7) -> list[tuple[str, str]]:
    rng = random.Random(seed)
    lines: list[str] = []
    for a, b in PAIRS:
        for drug in (a, b):
            tpl = TEMPLATES[len(lines) % len(TEMPLATES)]
            fills = {"drug": drug}
            if "{rx}" in tpl:
                rx = str(rng.randint(1, 9)) + "".join(str(rng.randint(0, 9)) for _ in range(6))
                fills["rx"] = digits_spoken(rx)
            if "{dob}" in tpl:
                d = date(rng.randint(1940, 1999), rng.randint(1, 12), rng.randint(1, 28))
                fills["dob"] = dob_spoken(d)
            lines.append(tpl.format(**fills))
    return [(f"c{i+1:02d}", t) for i, t in enumerate(lines)]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--print", dest="show", action="store_true")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    rows = build(args.seed)
    if args.show:
        for cid, text in rows:
            print(f"{cid}  {text}")
        return

    for cid, text in rows:
        (RECORDINGS / f"{cid}.txt").write_text(text + "\n")
    (RECORDINGS / "SCRIPT.md").write_text(
        "# Tier C recording script\n\n"
        f"{len(rows)} lines. Record each as `<id>.m4a` (or .wav) in this directory.\n"
        "The matching `.txt` is already written -- do not edit it unless you\n"
        "misspoke, in which case make the .txt match what you actually said.\n\n"
        + "".join(f"**{cid}** — {text}\n\n" for cid, text in rows)
    )
    print(f"wrote {len(rows)} label files + SCRIPT.md to {RECORDINGS}")


if __name__ == "__main__":
    main()
