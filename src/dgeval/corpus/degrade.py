"""Channel degradation -- turning studio audio into something phone-shaped.

A pharmacy refill line receives narrowband, codec-compressed, noisy audio. WER
measured on clean 16 kHz synthesis is not a number anyone can make a launch
decision from, so every clip is also rendered through conditions that match the
real transport:

  clean_16k              reference condition, 16 kHz mono
  telephony_8k_ulaw      8 kHz + G.711 mu-law round trip (classic PSTN)
  telephony_noisy_10db   the above, with pink noise added at an exact 10 dB SNR
                         *before* the codec, so the codec degrades the noise too
  opus_16k_reverb        room reverb + Opus at 16 kbps (VoIP / softphone)
  telephony_packetloss   8 kHz mu-law with 2% of 20 ms frames dropped

SNR is computed and applied numerically rather than left to an ffmpeg mixing
filter, so "10 dB" means 10 dB and the condition is reproducible.
"""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

TARGET_SR = 16000
FRAME_MS = 20


def _read(path: Path) -> tuple[np.ndarray, int]:
    x, sr = sf.read(str(path), dtype="float32", always_2d=False)
    if x.ndim > 1:
        x = x.mean(axis=1)
    return x, sr


def _write(path: Path, x: np.ndarray, sr: int) -> None:
    sf.write(str(path), np.clip(x, -1.0, 1.0), sr, subtype="PCM_16")


def pink_noise(n: int, seed: int) -> np.ndarray:
    """1/f noise via spectral shaping of white noise. Deterministic per seed."""
    rng = np.random.default_rng(seed)
    white = rng.standard_normal(n)
    spec = np.fft.rfft(white)
    freqs = np.fft.rfftfreq(n, d=1.0)
    scale = np.ones_like(freqs)
    scale[1:] = 1.0 / np.sqrt(freqs[1:])
    shaped = np.fft.irfft(spec * scale, n=n)
    return shaped / (np.max(np.abs(shaped)) + 1e-12)


def mix_at_snr(speech: np.ndarray, noise: np.ndarray, snr_db: float) -> np.ndarray:
    """Add noise scaled to hit an exact speech-to-noise ratio in dB."""
    if len(noise) < len(speech):
        noise = np.tile(noise, int(np.ceil(len(speech) / len(noise))))
    noise = noise[: len(speech)]
    rms_s = float(np.sqrt(np.mean(speech**2))) + 1e-12
    rms_n = float(np.sqrt(np.mean(noise**2))) + 1e-12
    target_rms_n = rms_s / (10 ** (snr_db / 20.0))
    return speech + noise * (target_rms_n / rms_n)


def _ffmpeg(args: list[str]) -> None:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], check=True)


def _ulaw_roundtrip(src: Path, dst: Path) -> None:
    """8 kHz G.711 mu-law encode then decode back to 16 kHz."""
    with tempfile.TemporaryDirectory() as td:
        mid = Path(td) / "ulaw.wav"
        _ffmpeg(["-i", str(src), "-ar", "8000", "-ac", "1", "-acodec", "pcm_mulaw", str(mid)])
        _ffmpeg(["-i", str(mid), "-ar", str(TARGET_SR), "-ac", "1", "-acodec", "pcm_s16le", str(dst)])


def _opus_roundtrip(src: Path, dst: Path, bitrate: str = "16k") -> None:
    with tempfile.TemporaryDirectory() as td:
        mid = Path(td) / "o.ogg"
        _ffmpeg(["-i", str(src), "-c:a", "libopus", "-b:a", bitrate, "-ac", "1", str(mid)])
        _ffmpeg(["-i", str(mid), "-ar", str(TARGET_SR), "-ac", "1", "-acodec", "pcm_s16le", str(dst)])


def _reverb(src: Path, dst: Path, amount: int = 40) -> None:
    subprocess.run(
        ["sox", str(src), str(dst), "reverb", str(amount), "50", "60"],
        check=True, capture_output=True,
    )


def _drop_frames(src: Path, dst: Path, loss_rate: float, seed: int) -> None:
    """Zero whole 20 ms frames -- what uncompensated packet loss sounds like."""
    x, sr = _read(src)
    frame = int(sr * FRAME_MS / 1000)
    rng = np.random.default_rng(seed)
    y = x.copy()
    for start in range(0, len(y) - frame, frame):
        if rng.random() < loss_rate:
            y[start:start + frame] = 0.0
    _write(dst, y, sr)


# --- condition registry -----------------------------------------------------

def clean_16k(src: Path, dst: Path, seed: int = 0) -> None:
    _ffmpeg(["-i", str(src), "-ar", str(TARGET_SR), "-ac", "1", "-acodec", "pcm_s16le", str(dst)])


def telephony_8k_ulaw(src: Path, dst: Path, seed: int = 0) -> None:
    _ulaw_roundtrip(src, dst)


def telephony_noisy_10db(src: Path, dst: Path, seed: int = 0) -> None:
    x, sr = _read(src)
    noisy = mix_at_snr(x, pink_noise(max(len(x), sr), seed), snr_db=10.0)
    with tempfile.TemporaryDirectory() as td:
        mid = Path(td) / "n.wav"
        _write(mid, noisy, sr)
        _ulaw_roundtrip(mid, dst)


def opus_16k_reverb(src: Path, dst: Path, seed: int = 0) -> None:
    with tempfile.TemporaryDirectory() as td:
        mid = Path(td) / "r.wav"
        _reverb(src, mid)
        _opus_roundtrip(mid, dst)


def telephony_packetloss(src: Path, dst: Path, seed: int = 0) -> None:
    with tempfile.TemporaryDirectory() as td:
        mid = Path(td) / "pl.wav"
        _drop_frames(src, mid, loss_rate=0.02, seed=seed)
        _ulaw_roundtrip(mid, dst)


CONDITIONS = {
    "clean_16k": clean_16k,
    "telephony_8k_ulaw": telephony_8k_ulaw,
    "telephony_noisy_10db": telephony_noisy_10db,
    "opus_16k_reverb": opus_16k_reverb,
    "telephony_packetloss": telephony_packetloss,
}

# The four conditions used for the headline degradation curve. Packet loss is
# available but held out of the default matrix to keep the run cheap.
DEFAULT_CONDITIONS = ["clean_16k", "telephony_8k_ulaw", "telephony_noisy_10db", "opus_16k_reverb"]


def apply(condition: str, src: Path, dst: Path, seed: int = 0) -> None:
    if dst.exists():
        return
    CONDITIONS[condition](src, dst, seed)


def duration_seconds(path: Path) -> float:
    info = sf.info(str(path))
    return info.frames / info.samplerate
