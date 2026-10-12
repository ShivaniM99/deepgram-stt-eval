"""Streaming transcription and latency measurement.

Why not just time a batch request
---------------------------------
Wall-clock on a pre-recorded upload measures file upload plus server
processing. It is not what a caller experiences and it is not what a customer
argues about in a design review. The number that matters on a phone line is:

    the caller stops speaking -> how long until the final transcript exists

which is dominated by endpointing, not by model speed. Measuring it requires
streaming audio at REAL TIME pace over a websocket. Audio pushed as fast as
the socket accepts it produces latency figures that are meaningless, because
the server has the whole utterance before it has finished "listening".

Four timings are captured per utterance:

    ttfi      first interim result, measured from the first audio byte sent
    endpoint  first is_final result, measured from the LAST audio byte sent
              -- this is the one a caller feels as dead air
    utt_end   the UtteranceEnd event, where enabled
    i2f       first interim -> first final

All are reported as p50/p95/p99, never as means: the tail is the complaint.

Measurement caveat, stated because it changes how the numbers should be read:
these are CLIENT-OBSERVED timings from a laptop on a residential connection.
They include network round trip in both directions. `measure_baseline_rtt()`
captures that floor so the report can present it alongside, and a
server-side-only figure would be lower. The numbers are honest as an upper
bound on what a user in this network position experiences, not as a
measurement of Deepgram's internal processing time.
"""
from __future__ import annotations

import asyncio
import json
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx
import numpy as np
import soundfile as sf
import websockets

from ..config import api_key

WS_URL = "wss://api.deepgram.com/v1/listen"
CHUNK_MS = 20          # 20 ms frames, matching typical RTP packetization


@dataclass(frozen=True)
class StreamConfig:
    model: str = "nova-3"
    language: str = "en"
    encoding: str = "linear16"
    sample_rate: int = 16000
    interim_results: bool = True
    smart_format: bool = True
    punctuate: bool = True
    endpointing: int | bool = 10          # ms of silence, or False to disable
    utterance_end_ms: int | None = 1000   # requires interim_results
    vad_events: bool = False
    keyterm: tuple[str, ...] = ()

    def query(self) -> str:
        p: list[tuple[str, str]] = [
            ("model", self.model), ("language", self.language),
            ("encoding", self.encoding), ("sample_rate", str(self.sample_rate)),
            ("interim_results", str(self.interim_results).lower()),
            ("smart_format", str(self.smart_format).lower()),
            ("punctuate", str(self.punctuate).lower()),
            ("vad_events", str(self.vad_events).lower()),
        ]
        p.append(("endpointing", "false" if self.endpointing is False
                  else str(int(self.endpointing))))
        if self.utterance_end_ms is not None:
            p.append(("utterance_end_ms", str(int(self.utterance_end_ms))))
        p += [("keyterm", k) for k in self.keyterm]
        return urlencode(p)

    def label(self) -> str:
        ep = "off" if self.endpointing is False else f"{int(self.endpointing)}ms"
        return f"endpointing={ep},utterance_end={self.utterance_end_ms}"


@dataclass
class StreamResult:
    uid: str
    transcript: str
    ttfi_ms: float | None            # first audio byte -> first interim
    endpoint_ms: float | None        # last audio byte -> first final
    utt_end_ms: float | None         # last audio byte -> UtteranceEnd
    i2f_ms: float | None             # first interim -> first final
    n_interims: int
    audio_s: float
    truncated: bool                  # final transcript shorter than any interim
    events: list[dict] = field(default_factory=list, repr=False)


def _load_pcm16(path: Path, target_sr: int) -> bytes:
    x, sr = sf.read(str(path), dtype="int16", always_2d=False)
    if x.ndim > 1:
        x = x[:, 0]
    if sr != target_sr:
        raise ValueError(f"{path.name} is {sr} Hz, stream config expects {target_sr}")
    return x.tobytes()


async def stream_one(path: Path, cfg: StreamConfig,
                     realtime: bool = True) -> StreamResult:
    pcm = _load_pcm16(path, cfg.sample_rate)
    bytes_per_chunk = int(cfg.sample_rate * 2 * CHUNK_MS / 1000)
    audio_s = len(pcm) / (cfg.sample_rate * 2)

    url = f"{WS_URL}?{cfg.query()}"
    headers = {"Authorization": f"Token {api_key()}"}

    t_first_byte: float | None = None
    t_last_byte: float | None = None
    t_first_interim: float | None = None
    t_first_final: float | None = None
    t_utt_end: float | None = None
    n_interims = 0
    best_interim_len = 0
    final_transcript = ""
    events: list[dict] = []

    async with websockets.connect(url, additional_headers=headers,
                                  max_size=8 * 1024 * 1024) as ws:

        async def send_audio() -> None:
            nonlocal t_first_byte, t_last_byte
            t0 = time.perf_counter()
            for i in range(0, len(pcm), bytes_per_chunk):
                chunk = pcm[i:i + bytes_per_chunk]
                if t_first_byte is None:
                    t_first_byte = time.perf_counter()
                await ws.send(chunk)
                if realtime:
                    # Pace to the wall clock rather than sleeping a fixed
                    # interval, so accumulated scheduling drift does not
                    # silently turn this into a faster-than-realtime send.
                    target = t0 + (i / bytes_per_chunk + 1) * CHUNK_MS / 1000
                    delay = target - time.perf_counter()
                    if delay > 0:
                        await asyncio.sleep(delay)
            t_last_byte = time.perf_counter()
            await ws.send(json.dumps({"type": "CloseStream"}))

        async def recv_events() -> None:
            nonlocal t_first_interim, t_first_final, t_utt_end
            nonlocal n_interims, best_interim_len, final_transcript
            async for raw in ws:
                now = time.perf_counter()
                try:
                    msg = json.loads(raw)
                except (TypeError, json.JSONDecodeError):
                    continue
                events.append({"t": now, **{k: msg.get(k) for k in
                                            ("type", "is_final", "speech_final")}})
                mtype = msg.get("type")

                if mtype == "UtteranceEnd":
                    t_utt_end = t_utt_end or now
                    continue
                if mtype != "Results":
                    continue

                try:
                    alt = msg["channel"]["alternatives"][0]
                except (KeyError, IndexError):
                    continue
                text = (alt.get("transcript") or "").strip()
                if not text:
                    continue

                if msg.get("is_final"):
                    if t_first_final is None:
                        t_first_final = now
                    final_transcript = (final_transcript + " " + text).strip()
                else:
                    n_interims += 1
                    best_interim_len = max(best_interim_len, len(text.split()))
                    if t_first_interim is None:
                        t_first_interim = now

        await asyncio.gather(send_audio(), recv_events())

    def ms(a: float | None, b: float | None) -> float | None:
        return None if (a is None or b is None) else (a - b) * 1000.0

    return StreamResult(
        uid=path.stem,
        transcript=final_transcript,
        ttfi_ms=ms(t_first_interim, t_first_byte),
        endpoint_ms=ms(t_first_final, t_last_byte),
        utt_end_ms=ms(t_utt_end, t_last_byte),
        i2f_ms=ms(t_first_final, t_first_interim),
        n_interims=n_interims,
        audio_s=audio_s,
        truncated=bool(final_transcript) and
                  len(final_transcript.split()) < best_interim_len,
        events=events,
    )


def measure_baseline_rtt(n: int = 5) -> dict[str, float]:
    """Network floor to the Deepgram edge, so latency can be read in context."""
    samples: list[float] = []
    with httpx.Client(timeout=10.0) as c:
        for _ in range(n):
            t0 = time.perf_counter()
            try:
                c.get("https://api.deepgram.com/v1/projects",
                      headers={"Authorization": f"Token {api_key()}"})
            except httpx.HTTPError:
                continue
            samples.append((time.perf_counter() - t0) * 1000)
    if not samples:
        return {}
    return {"rtt_p50_ms": statistics.median(samples),
            "rtt_min_ms": min(samples), "n": len(samples)}


def percentiles(values: list[float | None]) -> dict[str, float | None]:
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return {"n": 0, "p50": None, "p95": None, "p99": None, "mean": None}
    return {
        "n": len(vals),
        "p50": float(np.percentile(vals, 50)),
        "p95": float(np.percentile(vals, 95)),
        "p99": float(np.percentile(vals, 99)),
        "mean": float(np.mean(vals)),
    }
