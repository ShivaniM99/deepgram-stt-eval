"""Endpointing sweep: the latency/truncation tradeoff curve.

What this knob is
-----------------
`endpointing` is how many milliseconds of silence Deepgram waits before
declaring an utterance finished. It is the single parameter that most directly
sets what a caller experiences, and it is the one that gets tuned on a call
with a customer who says "the bot feels slow".

Why a sweep rather than a value
-------------------------------
Latency can always be improved by lowering it. That is not a free win: cut the
silence threshold too far and the endpoint fires inside a natural pause, the
utterance is truncated, and the tail of the sentence is lost. On this use case
the tail is where the Rx number lives -- "refill my hydroxyzine, prescription
number four four eight... [cut]".

So reporting a latency figure alone is misleading, and reporting a latency
figure that was obtained by tuning this knob down is actively dishonest. The
deliverable is the CURVE: endpoint latency against truncation rate across
threshold values, so the customer can choose a point on it against their own
tolerance rather than inheriting whichever value the evaluator happened to run.

A truncation here is detected structurally rather than by eye: the final
transcript contains fewer words than the longest interim result the server
already emitted for that utterance. If the server showed us more words and
then took them away, the endpoint fired early.
"""
from __future__ import annotations

import asyncio
import statistics
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

from ..asr.stream import StreamConfig, StreamResult, percentiles, stream_one

# Values spanning aggressive to conservative. 10 ms is Deepgram's default and
# is included as the reference point, not as a recommendation.
DEFAULT_SWEEP = [10, 100, 300, 500, 1000]


@dataclass
class SweepPoint:
    endpointing_ms: int
    utterance_end_ms: int | None
    n_clips: int
    endpoint_p50: float | None
    endpoint_p95: float | None
    endpoint_p99: float | None
    ttfi_p50: float | None
    ttfi_p95: float | None
    truncation_rate: float
    empty_rate: float
    mean_interims: float

    def as_row(self) -> dict[str, Any]:
        return asdict(self)


def summarize_point(endpointing_ms: int, utterance_end_ms: int | None,
                    results: list[StreamResult]) -> SweepPoint:
    ep = percentiles([r.endpoint_ms for r in results])
    tt = percentiles([r.ttfi_ms for r in results])
    n = len(results) or 1
    return SweepPoint(
        endpointing_ms=endpointing_ms,
        utterance_end_ms=utterance_end_ms,
        n_clips=len(results),
        endpoint_p50=ep["p50"], endpoint_p95=ep["p95"], endpoint_p99=ep["p99"],
        ttfi_p50=tt["p50"], ttfi_p95=tt["p95"],
        truncation_rate=sum(r.truncated for r in results) / n,
        empty_rate=sum(not r.transcript.strip() for r in results) / n,
        mean_interims=statistics.fmean([r.n_interims for r in results]) if results else 0.0,
    )


async def run_sweep(paths: list[Path], values: list[int] | None = None,
                    base: StreamConfig | None = None,
                    progress_cb=None) -> tuple[list[SweepPoint], dict[str, list[StreamResult]]]:
    """Stream every clip once per endpointing value, serially.

    Serial on purpose: concurrent realtime streams on one uplink contend for
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
        points.append(summarize_point(ep_ms, cfg.utterance_end_ms, results))

    return points, raw


def recommend(points: list[SweepPoint], max_truncation: float = 0.02
              ) -> dict[str, Any]:
    """Lowest-latency setting that still respects the truncation ceiling.

    Deliberately not "the fastest one". The recommendation is the fastest
    setting that does not lose audio, which is a different and much more
    defensible answer.
    """
    viable = [p for p in points if p.truncation_rate <= max_truncation
              and p.endpoint_p95 is not None]
    if not viable:
        return {"recommended_endpointing_ms": None,
                "reason": f"no swept value held truncation at or below "
                          f"{max_truncation:.0%}; the knob cannot satisfy this "
                          f"constraint and the audio path or VAD needs attention"}
    best = min(viable, key=lambda p: p.endpoint_p95)
    return {
        "recommended_endpointing_ms": best.endpointing_ms,
        "endpoint_p95_ms": best.endpoint_p95,
        "truncation_rate": best.truncation_rate,
        "reason": f"lowest p95 endpoint latency among settings holding "
                  f"truncation at or below {max_truncation:.0%}",
        "rejected": [
            {"endpointing_ms": p.endpointing_ms,
             "truncation_rate": p.truncation_rate,
             "endpoint_p95_ms": p.endpoint_p95}
            for p in points if p.truncation_rate > max_truncation
        ],
    }
