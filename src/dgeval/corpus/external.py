"""Tier B: real human speech from LibriSpeech dev-clean.

Why a public corpus is in here at all
-------------------------------------
Tier A is synthetic, so a WER measured on it alone tells you about the
synthesizer as much as about the ASR. LibriSpeech dev-clean supplies real human
speech with exact reference transcripts, giving a sanity anchor: if Deepgram
scores far off its published numbers on dev-clean, the harness is wrong, not
Deepgram. That makes this tier a calibration check on the measurement
apparatus, not just another data point.

Its limits are real and worth stating: dev-clean is read audiobook speech,
studio-quality and overwhelmingly US-accented. It carries no pharmacy
vocabulary. It is the control, not the test.

Licence: LibriSpeech is CC BY 4.0 (Panayotov et al., 2015), derived from
LibriVox public-domain recordings. Redistribution is fine; the download is
fetched at build time rather than vendored into the repo.
"""
from __future__ import annotations

import random
import subprocess
from pathlib import Path

from ..config import EXTERNAL

LIBRI_URL = "https://www.openslr.org/resources/12/dev-clean.tar.gz"
LIBRI_ROOT = EXTERNAL / "LibriSpeech" / "dev-clean"


def is_available() -> bool:
    return LIBRI_ROOT.exists() and any(LIBRI_ROOT.rglob("*.flac"))


def download_hint() -> str:
    return (
        f"LibriSpeech dev-clean not found at {LIBRI_ROOT}.\n"
        f"  cd {EXTERNAL} && curl -LO {LIBRI_URL} && tar xzf dev-clean.tar.gz\n"
        "Tier B is a calibration control; the harness runs without it but the "
        "report will say the control was skipped."
    )


def index() -> list[tuple[str, Path, str]]:
    """Return (utt_id, flac_path, reference_text) for every dev-clean utterance."""
    rows: list[tuple[str, Path, str]] = []
    for trans in LIBRI_ROOT.rglob("*.trans.txt"):
        for line in trans.read_text().splitlines():
            if not line.strip():
                continue
            utt_id, _, text = line.partition(" ")
            flac = trans.parent / f"{utt_id}.flac"
            if flac.exists():
                rows.append((utt_id, flac, text.strip()))
    return sorted(rows, key=lambda r: r[0])


def sample(n: int = 40, seed: int = 20261012,
           min_s: float = 2.0, max_s: float = 12.0) -> list[tuple[str, Path, str]]:
    """Sample n utterances, one per speaker where possible, in a usable duration band.

    One clip per speaker matters: dev-clean has ~40 speakers and sampling
    utterances uniformly would draw many clips from a few voices, turning a
    speaker-level result into a chapter-level one.
    """
    import soundfile as sf

    rows = index()
    by_speaker: dict[str, list] = {}
    for utt_id, flac, text in rows:
        spk = utt_id.split("-")[0]
        by_speaker.setdefault(spk, []).append((utt_id, flac, text))

    rng = random.Random(seed)
    picked: list[tuple[str, Path, str]] = []
    for spk in sorted(by_speaker):
        candidates = by_speaker[spk][:]
        rng.shuffle(candidates)
        for utt_id, flac, text in candidates:
            dur = sf.info(str(flac)).duration
            if min_s <= dur <= max_s:
                picked.append((utt_id, flac, text))
                break

    rng.shuffle(picked)
    return picked[:n]


def to_wav(src: Path, dst: Path, sr: int = 16000) -> None:
    if dst.exists():
        return
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
         "-ac", "1", "-ar", str(sr), "-acodec", "pcm_s16le", str(dst)],
        check=True,
    )
