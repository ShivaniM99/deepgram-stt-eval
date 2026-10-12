"""Tier D: non-speech audio, for measuring fabrication.

An ASR system fed audio containing no speech should return no words. Systems
that fail this fail it loudly -- inventing plausible sentences out of hold
music or line noise -- and on a pharmacy line a fabricated drug name is worse
than a dropped call, because it is silently wrong rather than obviously broken.

Pure digital silence is the easy case and on its own it is not a real test. A
phone line is never digitally silent: it carries room tone, comfort noise,
music, touch tones and crosstalk. Every probe here is SYNTHESIZED from
primitives rather than sampled from a recording, which keeps the corpus free of
third-party licensing and makes it exactly reproducible from a seed.

Scoring is simple and unforgiving: any non-empty normalized transcript on any
of these clips is an insertion. See metrics/scorers.py:hallucination_report.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

from .degrade import pink_noise

SR = 16000

DTMF_FREQS = {
    "1": (697, 1209), "2": (697, 1336), "3": (697, 1477),
    "4": (770, 1209), "5": (770, 1336), "6": (770, 1477),
    "7": (852, 1209), "8": (852, 1336), "9": (852, 1477),
    "0": (941, 1336), "*": (941, 1209), "#": (941, 1477),
}


def _t(dur: float) -> np.ndarray:
    return np.arange(int(SR * dur)) / SR


def _tone(freq: float, dur: float, amp: float = 0.3) -> np.ndarray:
    return amp * np.sin(2 * np.pi * freq * _t(dur))


def _db_to_amp(db: float) -> float:
    return 10 ** (db / 20.0)


def digital_silence(dur: float = 5.0, seed: int = 0) -> np.ndarray:
    """Exact zeros. The trivial case -- included as a floor, not as the test."""
    return np.zeros(int(SR * dur), dtype=np.float32)


def _at_rms_dbfs(x: np.ndarray, target_db: float) -> np.ndarray:
    """Scale a signal to an exact RMS level. pink_noise() is peak-normalized,
    so scaling it by an amplitude factor lands well below the intended level --
    levels here are specified as RMS because that is what "a quiet line"
    actually means."""
    rms = float(np.sqrt(np.mean(x**2))) + 1e-12
    return x * (_db_to_amp(target_db) / rms)


def room_tone(dur: float = 5.0, seed: int = 0) -> np.ndarray:
    """A quiet but live line: pink noise at -45 dBFS RMS."""
    return _at_rms_dbfs(pink_noise(int(SR * dur), seed), -45).astype(np.float32)


def line_noise(dur: float = 5.0, seed: int = 0) -> np.ndarray:
    """A noisy line: pink noise at -25 dBFS RMS, plus 60 Hz mains hum."""
    n = _at_rms_dbfs(pink_noise(int(SR * dur), seed), -25)
    return (n + _tone(60, dur, _db_to_amp(-38))).astype(np.float32)


def dial_tone(dur: float = 5.0, seed: int = 0) -> np.ndarray:
    """North American dial tone: 350 Hz + 440 Hz, continuous."""
    return (_tone(350, dur, 0.18) + _tone(440, dur, 0.18)).astype(np.float32)


def ring_tone(dur: float = 8.0, seed: int = 0) -> np.ndarray:
    """440 Hz + 480 Hz, 2 s on / 4 s off."""
    out = np.zeros(int(SR * dur), dtype=np.float32)
    period = int(SR * 6)
    burst = (_tone(440, 2.0, 0.18) + _tone(480, 2.0, 0.18)).astype(np.float32)
    for start in range(0, len(out), period):
        end = min(start + len(burst), len(out))
        out[start:end] += burst[: end - start]
    return out


def dtmf_sequence(dur: float = 5.0, seed: int = 0) -> np.ndarray:
    """Touch tones -- a caller keying through an IVR menu."""
    rng = np.random.default_rng(seed)
    digits = "".join(rng.choice(list("0123456789#*"), size=8))
    out = []
    for d in digits:
        f1, f2 = DTMF_FREQS[d]
        out.append((_tone(f1, 0.12, 0.25) + _tone(f2, 0.12, 0.25)))
        out.append(np.zeros(int(SR * 0.08)))
    sig = np.concatenate(out)
    reps = int(np.ceil(SR * dur / len(sig)))
    return np.tile(sig, reps)[: int(SR * dur)].astype(np.float32)


def hold_music(dur: float = 10.0, seed: int = 0) -> np.ndarray:
    """Synthesized on-hold music: a slow ii-V-I-vi progression with vibrato.

    Deliberately generated from sine primitives rather than sampled, so the
    corpus carries no third-party audio and no licensing question.
    """
    rng = np.random.default_rng(seed)
    chords = [[293.66, 349.23, 440.00], [392.00, 493.88, 587.33],
              [261.63, 329.63, 392.00], [220.00, 261.63, 329.63]]
    seg = dur / 8.0
    out = []
    for i in range(8):
        chord = chords[i % len(chords)]
        tt = _t(seg)
        vib = 1.0 + 0.004 * np.sin(2 * np.pi * 5.0 * tt)
        s = sum(0.09 * np.sin(2 * np.pi * f * vib * tt) for f in chord)
        env = np.minimum(1.0, np.linspace(0, 6, len(tt))) * \
              np.minimum(1.0, np.linspace(6, 0, len(tt)))
        out.append(s * env)
    sig = np.concatenate(out)
    sig += pink_noise(len(sig), seed) * _db_to_amp(-50)
    return (sig / (np.max(np.abs(sig)) + 1e-9) * 0.35).astype(np.float32)


def babble(dur: float = 6.0, seed: int = 0, speech_clips: list[Path] | None = None) -> np.ndarray:
    """Crosstalk: several speech clips summed so none is intelligible.

    This is the hardest honest probe in the tier -- there IS speech present,
    just none of it addressed to the line and none of it recoverable. A system
    may legitimately return something; what matters is whether it returns a
    confident drug name.
    """
    n = int(SR * dur)
    if not speech_clips:
        rng = np.random.default_rng(seed)
        base = pink_noise(n, seed)
        mod = 1.0 + 0.6 * np.sin(2 * np.pi * 3.0 * _t(dur) + rng.random())
        return (base * mod * _db_to_amp(-22)).astype(np.float32)
    acc = np.zeros(n, dtype=np.float32)
    for i, p in enumerate(speech_clips[:6]):
        x, sr = sf.read(str(p), dtype="float32")
        if x.ndim > 1:
            x = x.mean(axis=1)
        if len(x) < n:
            x = np.tile(x, int(np.ceil(n / len(x))))
        acc += np.roll(x[:n], i * SR // 3) * 0.35
    return (acc / (np.max(np.abs(acc)) + 1e-9) * 0.5).astype(np.float32)


PROBES = {
    "digital_silence": (digital_silence, 5.0),
    "room_tone": (room_tone, 5.0),
    "line_noise": (line_noise, 5.0),
    "dial_tone": (dial_tone, 5.0),
    "ring_tone": (ring_tone, 8.0),
    "dtmf_sequence": (dtmf_sequence, 5.0),
    "hold_music": (hold_music, 10.0),
    "babble": (babble, 6.0),
}


def build(out_dir: Path, repeats: int = 3, speech_clips: list[Path] | None = None
          ) -> list[dict]:
    """Render every probe `repeats` times with different seeds."""
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for name, (fn, dur) in PROBES.items():
        for k in range(repeats):
            path = out_dir / f"D_{name}_{k}.wav"
            if not path.exists():
                sig = (fn(dur, seed=1000 + k, speech_clips=speech_clips)
                       if name == "babble" else fn(dur, seed=1000 + k))
                sf.write(str(path), np.clip(sig, -1, 1), SR, subtype="PCM_16")
            rows.append({
                "uid": path.stem, "tier": "D_nonspeech", "probe": name,
                "path": str(path), "text": "", "entities": {},
                "duration_s": round(sf.info(str(path)).frames / SR, 3),
            })
    return rows
