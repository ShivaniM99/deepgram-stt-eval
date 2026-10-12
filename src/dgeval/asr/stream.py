"""Streaming transcription and latency measurement.

Why not just time a batch request
---------------------------------
Wall-clock on a pre-recorded upload measures file upload plus server
processing. It is not what a caller experiences. The number that matters on a
phone line is how long after the caller stops speaking the text exists, which
is dominated by endpointing, not model speed. Measuring it requires streaming
at REAL TIME pace: audio pushed as fast as the socket accepts produces
meaningless figures, because the server has the whole utterance before it has
finished "listening".

Three wrong metrics were tried first, against the live API
---------------------------------------------------------
Each looked plausible and measured nothing. They are recorded because the
final design only makes sense against them:

  1. "time to first is_final" -- gave latencies of MINUS three seconds.
     `is_final` does not mark the end of an utterance. Deepgram sets it on
     each finalized SEGMENT as the stream progresses, so the first one arrives
     while audio is still being sent.

  2. "time to speech_final, after streaming a silence tail" -- also wrong.
     `speech_final` and `UtteranceEnd` never arrived during a continuing
     stream, not even after 3 s of trailing silence with endpointing=300 ms.
     Both landed ~60 ms after CloseStream. The metric was measuring when the
     harness hung up.

  3. "...but with comfort noise instead of digital zeros", on the theory that
     the VAD needed signal to work against. Identical to zeros, within 2 ms.
     The behaviour is tied to stream close, not to audio content.

What is actually measured
-------------------------
Deepgram returns word-level timestamps, which makes the real question
answerable without involving stream close at all:

    finalization delay = (wall clock when the final arrived)
                       - (audio timestamp of its last word)

That is precisely "the caller finished saying this; when did the text exist?".
Finals arriving after CloseStream are flagged and EXCLUDED, since their timing
is an artifact of the harness hanging up rather than of the endpointer.

The second metric is fragmentation: how many final segments one utterance is
split into. This is where the real trade-off lives, and it is not the one
originally expected. Aggressive endpointing does not truncate -- it SPLITS.
Measured on a clip with a 470 ms internal pause:

    endpointing=10 ms   -> finalization in  314 ms, utterance split in two
    endpointing=300 ms  -> finalization in 2343 ms, utterance split in two

An agent acting on the first final of a split utterance holds half the
request: "I would like a refill on hydralazine fifty milligrams" arrives
without "prescription number one zero zero zero eight eight one". For this use
case that is worse than waiting, because the missing half carries the Rx
number.

Measurement caveat: these are CLIENT-OBSERVED timings from one machine,
inclusive of network round trip in both directions. `measure_baseline_rtt()`
captures that floor so the report can present it alongside. They bound what a
user in this network position experiences; they do not measure Deepgram's
internal processing time.
"""
from __future__ import annotations

import asyncio
import json
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path
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
    # Silence streamed after the speech, as a real line would carry. It does
    # NOT make speech_final fire (see module docstring), but it lets a late
    # natural finalization land before the close and keeps the close-forced
    # final clearly separated in time from the natural ones.
    silence_tail_ms: int = 2000

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
        return f"endpointing={ep}"


@dataclass
class FinalSegment:
    """One is_final result, with enough context to time it honestly."""
    recv_s: float              # seconds from stream start
    last_word_end_s: float     # audio timestamp of its final word
    delay_ms: float            # recv_s - last_word_end_s, in ms
    n_words: int
    transcript: str
    forced_by_close: bool      # arrived after CloseStream -> excluded


@dataclass
class StreamResult:
    uid: str
    transcript: str
    ttfi_ms: float | None = None
    segments: list[FinalSegment] = field(default_factory=list)
    n_interims: int = 0
    audio_s: float = 0.0
    events: list[dict] = field(default_factory=list, repr=False)

    @property
    def natural(self) -> list[FinalSegment]:
        return [s for s in self.segments if not s.forced_by_close]

    @property
    def finalization_delays_ms(self) -> list[float]:
        return [s.delay_ms for s in self.natural]

    @property
    def n_segments(self) -> int:
        """Pieces the utterance arrived in, close-forced included: fragmentation
        is about how many parts there were, whatever ended each one."""
        return len(self.segments)

    @property
    def fragmented(self) -> bool:
        return self.n_segments > 1


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

    state: dict = {"t_first_byte": None, "t_close": None, "t_first_interim": None,
                   "n_interims": 0, "transcript": ""}
    segments: list[FinalSegment] = []
    events: list[dict] = []

    async with websockets.connect(url, additional_headers=headers,
                                  max_size=8 * 1024 * 1024) as ws:
        t0 = time.perf_counter()

        async def send_audio() -> None:
            sent = 0
            for i in range(0, len(pcm), bytes_per_chunk):
                if state["t_first_byte"] is None:
                    state["t_first_byte"] = time.perf_counter()
                await ws.send(pcm[i:i + bytes_per_chunk])
                sent += 1
                if realtime:
                    # Pace against the wall clock rather than a fixed sleep, so
                    # scheduling drift cannot quietly make this faster than real
                    # time and invalidate every timing below.
                    delay = t0 + sent * CHUNK_MS / 1000 - time.perf_counter()
                    if delay > 0:
                        await asyncio.sleep(delay)

            silent = b"\x00" * bytes_per_chunk
            for _ in range(int(cfg.silence_tail_ms / CHUNK_MS)):
                await ws.send(silent)
                sent += 1
                if realtime:
                    delay = t0 + sent * CHUNK_MS / 1000 - time.perf_counter()
                    if delay > 0:
                        await asyncio.sleep(delay)

            state["t_close"] = time.perf_counter()
            await ws.send(json.dumps({"type": "CloseStream"}))

        async def recv_events() -> None:
            async for raw in ws:
                now = time.perf_counter()
                try:
                    msg = json.loads(raw)
                except (TypeError, json.JSONDecodeError):
                    continue
                events.append({"t": round(now - t0, 4),
                               **{k: msg.get(k) for k in
                                  ("type", "is_final", "speech_final")}})
                if msg.get("type") != "Results":
                    continue
                try:
                    alt = msg["channel"]["alternatives"][0]
                except (KeyError, IndexError):
                    continue
                text = (alt.get("transcript") or "").strip()
                if not text:
                    continue

                if msg.get("is_final"):
                    state["transcript"] = (state["transcript"] + " " + text).strip()
                    words = alt.get("words") or []
                    if not words:
                        continue
                    recv_s = now - t0
                    last_end = float(words[-1]["end"])
                    segments.append(FinalSegment(
                        recv_s=recv_s,
                        last_word_end_s=last_end,
                        delay_ms=(recv_s - last_end) * 1000.0,
                        n_words=len(words),
                        transcript=text,
                        forced_by_close=(state["t_close"] is not None
                                         and now >= state["t_close"]),
                    ))
                else:
                    state["n_interims"] += 1
                    if state["t_first_interim"] is None:
                        state["t_first_interim"] = now

        await asyncio.gather(send_audio(), recv_events())

    tfi, tfb = state["t_first_interim"], state["t_first_byte"]
    return StreamResult(
        uid=path.stem,
        transcript=state["transcript"],
        ttfi_ms=None if (tfi is None or tfb is None) else (tfi - tfb) * 1000.0,
        segments=segments,
        n_interims=state["n_interims"],
        audio_s=audio_s,
        events=events,
    )


def measure_baseline_rtt(n: int = 5) -> dict[str, float]:
    """Network floor to the Deepgram edge, so latency reads in context."""
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
