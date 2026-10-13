"""Endpointing sweep: the latency/fragmentation tradeoff curve.

What this knob is
-----------------
`endpointing` is how many milliseconds of silence Deepgram waits before
finalizing a segment. It is the parameter that most directly sets what a caller
experiences, and the one that gets tuned on a call with a customer who says
"the bot feels slow".

Why a sweep rather than a value
-------------------------------
Latency can always be improved by lowering it, and the cost is not free. The
cost is not what was expected, either: measuring against the live API showed
that aggressive endpointing does not TRUNCATE the utterance, it SPLITS it.

    endpointing=10 ms   -> text in  311 ms, utterance arrives in 2 pieces
    endpointing=300 ms  -> text in 2333 ms, utterance arrives in 2 pieces

A split matters here more than slowness does. The first final of a split
utterance reads "I'd like a refill on hydralazine fifty milligrams" and the Rx
number is in the second piece. An agent that acts on the first final has a
drug name and no prescription to attach it to; one that waits for the whole
utterance is slower but correct. So the deliverable is the CURVE -- delay
against fragmentation -- and the recommendation is the fastest setting that
keeps utterances whole, not the fastest setting.

Only NATURAL finalizations are timed. A final arriving after CloseStream is
timed by when the harness hung up, not by the endpointer, so it is excluded.
See asr/stream.py for the three wrong metrics that preceded this one.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np

from ..asr.stream import StreamConfig, StreamResult, percentiles, stream_one

# Aggressive through conservative. 10 ms is Deepgram's default and is included
# as the reference point, not as a recommendation.
DEFAULT_SWEEP = [10, 100, 300, 500, 1000]


@dataclass
class SweepPoint:
    endpointing_ms: int
    n_clips: int
    final_p50: float | None          # finalization delay, natural finals only
    final_p95: float | None
    final_p99: float | None
    ttfi_p50: float | None
    fragmentation_rate: float        # share of clips arriving in >1 piece
    mean_segments: float
    empty_rate: float
    n_natural_finals: int

    def as_row(self) -> dict[str, Any]:
        return asdict(self)


def summarize_point(endpointing_ms: int, results: list[StreamResult]) -> SweepPoint:
    delays = [d for r in results for d in r.finalization_delays_ms]
    fp = percentiles(delays)
    tt = percentiles([r.ttfi_ms for r in results])
    n = len(results) or 1
    return SweepPoint(
        endpointing_ms=endpointing_ms,
        n_clips=len(results),
        final_p50=fp["p50"], final_p95=fp["p95"], final_p99=fp["p99"],
        ttfi_p50=tt["p50"],
        fragmentation_rate=sum(r.fragmented for r in results) / n,
        mean_segments=float(np.mean([r.n_segments for r in results])) if results else 0.0,
        empty_rate=sum(not r.transcript.strip() for r in results) / n,
        n_natural_finals=len(delays),
    )


async def run_sweep(paths: list[Path], values: list[int] | None = None,
                    base: StreamConfig | None = None, progress_cb=None
                    ) -> tuple[list[SweepPoint], dict[str, list[StreamResult]]]:
    """Stream every clip once per endpointing value, serially.

    Serial on purpose: concurrent real-time streams on one uplink contend for
    bandwidth and inflate exactly the timings being measured.
    """
    values = values or DEFAULT_SWEEP
    base = base or StreamConfig()
    points: list[SweepPoint] = []
    raw: dict[str, list[StreamResult]] = {}

    for ep_ms in values:
        cfg = StreamConfig(**{**base.__dict__, "endpointing": ep_ms})
        results: list[StreamResult] = []
        for i, p in enumerate(paths):
            try:
                results.append(await stream_one(p, cfg, realtime=True))
            except Exception as exc:                      # noqa: BLE001
                if progress_cb:
                    progress_cb(ep_ms, i, None, exc)
                continue
            if progress_cb:
                progress_cb(ep_ms, i, results[-1], None)
        raw[str(ep_ms)] = results
        points.append(summarize_point(ep_ms, results))

    return points, raw


def recommend(points: list[SweepPoint], max_fragmentation: float = 0.10
              ) -> dict[str, Any]:
    """Lowest finalization delay that keeps utterances whole.

    Deliberately not "the fastest". A setting that returns text in 300 ms but
    splits one request across two finals is worse for this use case than one
    that takes two seconds and delivers it intact, because the second piece is
    where the prescription number lives.
    """
    viable = [p for p in points
              if p.fragmentation_rate <= max_fragmentation and p.final_p95 is not None]
    if not viable:
        best_frag = min((p.fragmentation_rate for p in points), default=None)
        return {
            "recommended_endpointing_ms": None,
            "reason": (f"no swept value held fragmentation at or below "
                       f"{max_fragmentation:.0%} (best was "
                       f"{best_frag:.0%} if measured). Utterances in this corpus "
                       f"contain internal pauses long enough to finalize on, so "
                       f"the knob alone cannot keep them whole. The call flow "
                       f"must reassemble consecutive finals before acting, or "
                       f"utterance_end_ms must be used to delimit turns."),
            "fragmentation_by_setting": [
                {"endpointing_ms": p.endpointing_ms,
                 "fragmentation_rate": p.fragmentation_rate,
                 "final_p95_ms": p.final_p95} for p in points],
        }
    best = min(viable, key=lambda p: p.final_p95)
    return {
        "recommended_endpointing_ms": best.endpointing_ms,
        "final_p95_ms": best.final_p95,
        "fragmentation_rate": best.fragmentation_rate,
        "reason": (f"lowest p95 finalization delay among settings keeping "
                   f"fragmentation at or below {max_fragmentation:.0%}"),
        "rejected": [
            {"endpointing_ms": p.endpointing_ms,
             "fragmentation_rate": p.fragmentation_rate,
             "final_p95_ms": p.final_p95}
            for p in points if p.fragmentation_rate > max_fragmentation],
    }
