"""Speech synthesis for the domain corpus.

Engine choice, and why it changed
---------------------------------
This started on `edge-tts`, which is a reverse-engineered client for a
Microsoft consumer endpoint. It began returning HTTP 403 mid-build. That is
the expected failure mode for an unofficial endpoint, and a dependency that
breaks when a reviewer clones the repo is worse than a slightly lower-fidelity
one, so it was dropped rather than pinned and prayed over.

What replaced it:

  macOS `say`  -- primary. Offline, free, deterministic, and ships with real
                  accent coverage (en_US / en_GB / en_IN / en_AU / en_IE /
                  en_ZA). No network, so corpus builds are reproducible.
  Deepgram Aura -- secondary cross-check. ~$0.03 per 1k characters, so the
                  whole corpus is well under a dollar.

Two unrelated synthesizers are used on purpose: any single TTS stack has its
own vocoder artifacts, and an ASR system can be unusually good or bad on
exactly those. Measuring across two keeps a finding from being an artifact of
one synthesizer.

One caveat stated plainly, because it matters for how the results are read:
Aura is a Deepgram model, so scoring Deepgram STT on Aura audio is mildly
circular. Headline numbers therefore come from `say` (an unrelated vendor) and
from tier C real recordings. Aura is used only as a cross-engine sanity check,
and any metric that disagrees between engines is reported rather than averaged.

Synthetic speech is not a substitute for real speech under any of this. It
lacks disfluency, breath, variable rate, and genuine channel interaction. That
is the explicit limitation in the memo and the reason tier C exists.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import httpx

TARGET_SR = 16000

# locale is carried through to the manifest so results can be sliced by accent.
SAY_VOICES: list[tuple[str, str]] = [
    ("Samantha", "en_US"),
    ("Kathy", "en_US"),
    ("Daniel", "en_GB"),
    ("Moira", "en_IE"),
    ("Karen", "en_AU"),
    ("Rishi", "en_IN"),
    ("Aman", "en_IN"),
    ("Tara", "en_IN"),
    ("Tessa", "en_ZA"),
]

# Sampling weights: a US pharmacy line is majority US-accented, with a
# meaningful non-native minority. Uniform sampling would overstate the
# non-native share and make the headline WER pessimistic.
SAY_WEIGHTS = [3, 2, 1, 1, 1, 2, 1, 1, 1]

AURA_VOICES = ["aura-2-thalia-en", "aura-2-apollo-en", "aura-2-andromeda-en"]

AURA_URL = "https://api.deepgram.com/v1/speak"


def _to_wav(src: Path, dst: Path, sr: int = TARGET_SR) -> None:
    """Normalize any input to mono PCM16 at the pipeline sample rate."""
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
         "-ac", "1", "-ar", str(sr), "-acodec", "pcm_s16le", str(dst)],
        check=True,
    )


def synth_say(text: str, voice: str, out_wav: Path) -> bool:
    """macOS `say`. Returns False if the clip already existed (no work done)."""
    if out_wav.exists():
        return False
    aiff = out_wav.with_suffix(".aiff")
    try:
        subprocess.run(["say", "-v", voice, "-o", str(aiff), text], check=True,
                       capture_output=True)
        _to_wav(aiff, out_wav)
    finally:
        aiff.unlink(missing_ok=True)
    return True


def synth_aura(text: str, model: str, out_wav: Path, api_key: str) -> bool:
    """Deepgram Aura TTS. Billed per character -- see module docstring."""
    if out_wav.exists():
        return False
    mp3 = out_wav.with_suffix(".aura.mp3")
    try:
        r = httpx.post(
            AURA_URL,
            params={"model": model, "encoding": "mp3"},
            headers={"Authorization": f"Token {api_key}",
                     "Content-Type": "application/json"},
            json={"text": text},
            timeout=60.0,
        )
        r.raise_for_status()
        mp3.write_bytes(r.content)
        _to_wav(mp3, out_wav)
    finally:
        mp3.unlink(missing_ok=True)
    return True


def aura_cost_usd(texts: list[str], price_per_1k: float = 0.030) -> float:
    return sum(len(t) for t in texts) / 1000.0 * price_per_1k


def available_say_voices() -> set[str]:
    out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True).stdout
    return {line.split()[0] for line in out.splitlines() if line.strip()}
