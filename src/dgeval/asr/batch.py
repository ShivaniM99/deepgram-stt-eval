"""Deepgram pre-recorded (batch) transcription, cached and budget-guarded.

Every call goes through the content-addressed cache in dgeval.cache, keyed on
(audio bytes, full request config). Consequences:

  * re-running an experiment is free and reproducible
  * scoring logic can be rewritten dozens of times against responses that were
    paid for once
  * changing ANY request parameter produces a new key, so a cached result can
    never silently stand in for a different configuration

The budget guard counts audio minutes before the request is made, not after,
so a runaway loop aborts rather than reporting its own overspend.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import httpx

from .. import cache
from ..config import BudgetGuard, api_key

LISTEN_URL = "https://api.deepgram.com/v1/listen"


@dataclass(frozen=True)
class AsrConfig:
    """A Deepgram request configuration.

    Defaults are the ones a customer would plausibly ship with for a phone
    line, not the ones that flatter the numbers. `keyterm` is the Nova-3
    keyterm-prompting parameter and is empty by default so the baseline is
    measured without it -- the keyterm experiment turns it on and measures
    both the lift and the cost.
    """
    model: str = "nova-3"
    language: str = "en"
    smart_format: bool = True
    punctuate: bool = True
    numerals: bool = False
    diarize: bool = False
    keyterm: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    extra: tuple[tuple[str, str], ...] = ()

    def params(self) -> list[tuple[str, str]]:
        """Deepgram takes repeated keyterm/keywords params, so this is a list
        of pairs rather than a dict."""
        p: list[tuple[str, str]] = [
            ("model", self.model),
            ("language", self.language),
            ("smart_format", str(self.smart_format).lower()),
            ("punctuate", str(self.punctuate).lower()),
            ("numerals", str(self.numerals).lower()),
            ("diarize", str(self.diarize).lower()),
        ]
        p += [("keyterm", k) for k in self.keyterm]
        p += [("keywords", k) for k in self.keywords]
        p += list(self.extra)
        return p

    def key(self) -> dict[str, Any]:
        return {"endpoint": "listen", **{k: v for k, v in self.params()},
                "keyterm_list": list(self.keyterm),
                "keywords_list": list(self.keywords)}

    def label(self) -> str:
        bits = [self.model]
        if self.keyterm:
            bits.append(f"keyterm[{len(self.keyterm)}]")
        if self.keywords:
            bits.append(f"keywords[{len(self.keywords)}]")
        if self.numerals:
            bits.append("numerals")
        if not self.smart_format:
            bits.append("no_smart_format")
        return "+".join(bits)


@dataclass
class AsrResult:
    uid: str
    transcript: str
    confidence: float | None
    words: list[dict]
    request_s: float
    from_cache: bool
    raw: dict = field(repr=False, default_factory=dict)


def _extract(raw: dict) -> tuple[str, float | None, list[dict]]:
    try:
        alt = raw["results"]["channels"][0]["alternatives"][0]
    except (KeyError, IndexError):
        return "", None, []
    return alt.get("transcript", ""), alt.get("confidence"), alt.get("words", [])


def transcribe_one(path: Path, cfg: AsrConfig, guard: BudgetGuard,
                   duration_s: float, client: httpx.Client | None = None) -> AsrResult:
    ahash = cache.audio_hash(path)
    hit = cache.get(ahash, cfg.key())
    if hit is not None:
        tr, conf, words = _extract(hit)
        return AsrResult(path.stem, tr, conf, words, 0.0, True, hit)

    guard.charge(duration_s / 60.0)

    own = client is None
    client = client or httpx.Client(timeout=120.0)
    try:
        t0 = time.perf_counter()
        r = client.post(
            LISTEN_URL,
            params=cfg.params(),
            headers={"Authorization": f"Token {api_key()}",
                     "Content-Type": "audio/wav"},
            content=path.read_bytes(),
        )
        elapsed = time.perf_counter() - t0
        r.raise_for_status()
        raw = r.json()
    finally:
        if own:
            client.close()

    cache.put(ahash, cfg.key(), raw)
    tr, conf, words = _extract(raw)
    return AsrResult(path.stem, tr, conf, words, elapsed, False, raw)


def transcribe_many(rows: Iterable[dict], cfg: AsrConfig, guard: BudgetGuard,
                    progress_cb=None) -> list[AsrResult]:
    """Sequential on purpose.

    Deepgram would happily take these in parallel, but a serial pass makes the
    budget guard exact and keeps request timings interpretable. Latency is
    measured properly in asr/stream.py against the streaming endpoint; the
    `request_s` recorded here is wall-clock for a batch upload and is NOT a
    latency metric -- it is reported separately and labelled as such.
    """
    out: list[AsrResult] = []
    with httpx.Client(timeout=120.0) as client:
        for i, row in enumerate(rows):
            res = transcribe_one(Path(row["path"]), cfg, guard,
                                 row["duration_s"], client=client)
            out.append(res)
            if progress_cb:
                progress_cb(i, res)
    return out
