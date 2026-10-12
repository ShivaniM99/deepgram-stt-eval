#!/usr/bin/env python
"""Stream a subset of clips at several endpointing values; emit the tradeoff curve.

Streaming is billed by connection time and runs at real time by necessity, so
this deliberately uses a SUBSET rather than the whole corpus: 20 clips x 5
settings x ~6 s is about 10 minutes of audio, roughly $0.05, and about 10
minutes of wall clock.

  python scripts/03_endpointing_sweep.py --n 20
  python scripts/03_endpointing_sweep.py --n 20 --values 10 300 1000
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dgeval.asr.stream import StreamConfig, measure_baseline_rtt
from dgeval.config import MANIFEST, RESULTS, has_api_key
from dgeval.experiments.endpointing import DEFAULT_SWEEP, recommend, run_sweep
from rich.console import Console
from rich.table import Table

console = Console()


def pick_clips(n: int, condition: str) -> list[Path]:
    """Domain clips on the real transport, with an Rx number at the end.

    Rx-bearing utterances are chosen on purpose: they put the digits last,
    which is exactly where early endpointing does its damage.
    """
    rows = [json.loads(l) for l in MANIFEST.read_text().splitlines() if l.strip()]
    rows = [r for r in rows
            if r["tier"] == "A_domain_synthetic"
            and r["condition"] == condition
            and "rx" in (r.get("entities") or {})]
    rows.sort(key=lambda r: r["uid"])
    return [Path(r["path"]) for r in rows[:n]]


async def amain(args) -> None:
    paths = pick_clips(args.n, args.condition)
    if not paths:
        raise SystemExit("no matching clips; run scripts/01_build_corpus.py first")

    rtt = measure_baseline_rtt()
    if rtt:
        console.print(f"[dim]network floor to Deepgram: "
                      f"p50 {rtt['rtt_p50_ms']:.0f} ms, min {rtt['rtt_min_ms']:.0f} ms "
                      f"(n={rtt['n']}) -- all latencies below include this[/]")

    est_min = sum(1 for _ in paths) * len(args.values) * 6 / 60
    console.print(f"{len(paths)} clips x {len(args.values)} settings "
                  f"~= {est_min:.0f} min streaming, ~${est_min * 0.29/60:.3f}, "
                  f"real time so expect ~{est_min:.0f} min wall clock")

    def cb(ep, i, res, exc):
        if exc:
            console.print(f"  [yellow]{ep}ms clip {i}: {type(exc).__name__}: {exc}[/]")

    base = StreamConfig(model=args.model, utterance_end_ms=args.utterance_end_ms)
    points, raw = await run_sweep(paths, args.values, base, progress_cb=cb)

    t = Table(title="endpointing sweep: latency vs truncation", show_edge=False)
    for c in ("endpointing", "n", "endpoint p50", "p95", "p99",
              "ttfi p50", "truncated", "empty"):
        t.add_column(c, justify="right")
    for p in points:
        f = lambda v: "-" if v is None else f"{v:.0f}"
        t.add_row(f"{p.endpointing_ms} ms", str(p.n_clips),
                  f(p.endpoint_p50), f(p.endpoint_p95), f(p.endpoint_p99),
                  f(p.ttfi_p50), f"{p.truncation_rate:.1%}", f"{p.empty_rate:.1%}")
    console.print(t)

    rec = recommend(points, max_truncation=args.max_truncation)
    console.print(f"\n[bold]recommendation:[/] {rec}")
    console.print("[dim]note: the fastest setting is deliberately NOT the "
                  "recommendation -- see experiments/endpointing.py[/]")

    (RESULTS / "endpointing.json").write_text(json.dumps({
        "baseline_rtt": rtt,
        "condition": args.condition,
        "points": [p.as_row() for p in points],
        "recommendation": rec,
        "per_clip": {k: [{"uid": r.uid, "endpoint_ms": r.endpoint_ms,
                          "ttfi_ms": r.ttfi_ms, "truncated": r.truncated,
                          "transcript": r.transcript} for r in v]
                     for k, v in raw.items()},
    }, indent=2))
    console.print(f"results -> {RESULTS / 'endpointing.json'}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--values", type=int, nargs="+", default=DEFAULT_SWEEP)
    ap.add_argument("--condition", default="telephony_8k_ulaw")
    ap.add_argument("--utterance-end-ms", type=int, default=1000)
    ap.add_argument("--max-truncation", type=float, default=0.02)
    ap.add_argument("--model", default="nova-3")
    args = ap.parse_args()
    if not has_api_key():
        raise SystemExit("DEEPGRAM_API_KEY not set; see .env.example")
    asyncio.run(amain(args))


if __name__ == "__main__":
    main()
