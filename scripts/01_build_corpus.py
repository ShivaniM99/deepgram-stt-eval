#!/usr/bin/env python
"""Build the evaluation corpus and write data/corpus/manifest.jsonl.

Four tiers, assembled independently so any one can be skipped:

  A  domain synthetic   templated pharmacy utterances, exact entity labels
  B  real human control LibriSpeech dev-clean, calibrates the harness itself
  C  real human domain  your own recordings, dropped into data/recordings/
  D  non-speech         synthesized probes for fabrication

Tiers A-C are each rendered through every channel condition, which is what
produces a degradation curve rather than a single number. Tier D is already
the probe and is left at source quality.

Idempotent: every stage skips files that already exist, so re-running after
adding tier C recordings only does the new work.

  python scripts/01_build_corpus.py --n-domain 100 --n-libri 40
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dgeval.config import AUDIO, MANIFEST, RECORDINGS
from dgeval.corpus import degrade, external, nonspeech, tts
from dgeval.corpus.generate import coverage, generate
from rich.console import Console
from rich.table import Table

console = Console()


def build_tier_a(n: int, conditions: list[str], seed: int) -> list[dict]:
    utts = generate(n, seed=seed)
    console.print(f"[bold]Tier A[/] {len(utts)} domain utterances  "
                  f"{json.dumps(coverage(utts))}")

    rng = random.Random(seed)
    voices = rng.choices(tts.SAY_VOICES, weights=tts.SAY_WEIGHTS, k=len(utts))

    rows: list[dict] = []
    for u, (voice, locale) in zip(utts, voices):
        base = AUDIO / f"{u.uid}_base.wav"
        tts.synth_say(u.text, voice, base)
        for cond in conditions:
            out = AUDIO / f"{u.uid}_{cond}.wav"
            degrade.apply(cond, base, out, seed=hash(u.uid) % 10_000)
            rows.append({
                "uid": f"{u.uid}_{cond}", "base_uid": u.uid, "tier": u.tier,
                "condition": cond, "path": str(out), "text": u.text,
                "entities": u.entities, "voice": voice, "locale": locale,
                "template_id": u.template_id,
                "duration_s": round(degrade.duration_seconds(out), 3),
            })
    return rows


def build_tier_b(n: int, conditions: list[str], seed: int) -> list[dict]:
    if not external.is_available():
        console.print(f"[yellow]Tier B skipped[/]\n{external.download_hint()}")
        return []
    picked = external.sample(n, seed=seed)
    console.print(f"[bold]Tier B[/] {len(picked)} LibriSpeech clips "
                  f"from {len({p[0].split('-')[0] for p in picked})} speakers")
    rows: list[dict] = []
    for utt_id, flac, text in picked:
        base = AUDIO / f"B_{utt_id}_base.wav"
        external.to_wav(flac, base)
        for cond in conditions:
            out = AUDIO / f"B_{utt_id}_{cond}.wav"
            degrade.apply(cond, base, out, seed=hash(utt_id) % 10_000)
            rows.append({
                "uid": f"B_{utt_id}_{cond}", "base_uid": f"B_{utt_id}",
                "tier": "B_real_control", "condition": cond, "path": str(out),
                "text": text, "entities": {}, "voice": utt_id.split("-")[0],
                "locale": "en_US_read", "template_id": None,
                "duration_s": round(degrade.duration_seconds(out), 3),
            })
    return rows


def build_tier_c(conditions: list[str]) -> list[dict]:
    """Real recordings dropped into data/recordings/.

    Expects <name>.wav|m4a|mp3 alongside <name>.txt holding what was said.
    Absent a .txt the clip is skipped with a warning -- an unlabelled clip
    cannot be scored and silently including it would corrupt the aggregate.
    """
    media = [p for p in sorted(RECORDINGS.iterdir())
             if p.suffix.lower() in {".wav", ".m4a", ".mp3", ".flac"}]
    if not media:
        console.print("[yellow]Tier C skipped[/] no recordings in data/recordings/ "
                      "(see data/recordings/README.md for the script to read)")
        return []
    rows: list[dict] = []
    labelled = 0
    for m in media:
        ref = m.with_suffix(".txt")
        if not ref.exists():
            console.print(f"  [yellow]no transcript for {m.name}, skipping[/]")
            continue
        labelled += 1
        text = ref.read_text().strip()
        base = AUDIO / f"C_{m.stem}_base.wav"
        external.to_wav(m, base)
        for cond in conditions:
            out = AUDIO / f"C_{m.stem}_{cond}.wav"
            degrade.apply(cond, base, out, seed=hash(m.stem) % 10_000)
            rows.append({
                "uid": f"C_{m.stem}_{cond}", "base_uid": f"C_{m.stem}",
                "tier": "C_real_domain", "condition": cond, "path": str(out),
                "text": text, "entities": {}, "voice": "self_recorded",
                "locale": "self", "template_id": None,
                "duration_s": round(degrade.duration_seconds(out), 3),
            })
    console.print(f"[bold]Tier C[/] {labelled} labelled recordings")
    return rows


def build_tier_d(repeats: int) -> list[dict]:
    speech = sorted(AUDIO.glob("A*_clean_16k.wav"))[:6]
    rows = nonspeech.build(AUDIO, repeats=repeats, speech_clips=speech or None)
    for r in rows:
        r.setdefault("base_uid", r["uid"])
        r.setdefault("condition", "source")
        r.setdefault("voice", None)
        r.setdefault("locale", None)
        r.setdefault("template_id", None)
    console.print(f"[bold]Tier D[/] {len(rows)} non-speech probes "
                  f"({len(nonspeech.PROBES)} kinds x {repeats})")
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-domain", type=int, default=100)
    ap.add_argument("--n-libri", type=int, default=40)
    ap.add_argument("--d-repeats", type=int, default=3)
    ap.add_argument("--seed", type=int, default=20261012)
    ap.add_argument("--conditions", nargs="*", default=degrade.DEFAULT_CONDITIONS)
    ap.add_argument("--tiers", default="ABCD")
    args = ap.parse_args()

    for c in args.conditions:
        if c not in degrade.CONDITIONS:
            raise SystemExit(f"unknown condition {c!r}; "
                             f"available: {sorted(degrade.CONDITIONS)}")

    rows: list[dict] = []
    if "A" in args.tiers:
        rows += build_tier_a(args.n_domain, args.conditions, args.seed)
    if "B" in args.tiers:
        rows += build_tier_b(args.n_libri, args.conditions, args.seed)
    if "C" in args.tiers:
        rows += build_tier_c(args.conditions)
    if "D" in args.tiers:
        rows += build_tier_d(args.d_repeats)

    MANIFEST.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))

    total_s = sum(r["duration_s"] for r in rows)
    t = Table(title="corpus summary", show_edge=False)
    for col in ("tier", "clips", "minutes", "cost per pass"):
        t.add_column(col, justify="right" if col != "tier" else "left")
    for tier in sorted({r["tier"] for r in rows}):
        sub = [r for r in rows if r["tier"] == tier]
        mins = sum(r["duration_s"] for r in sub) / 60
        t.add_row(tier, str(len(sub)), f"{mins:.1f}", f"${mins * 0.0043:.3f}")
    t.add_row("[bold]TOTAL", f"[bold]{len(rows)}", f"[bold]{total_s/60:.1f}",
              f"[bold]${total_s/60*0.0043:.3f}")
    console.print(t)
    console.print(f"manifest -> {MANIFEST}")


if __name__ == "__main__":
    main()
