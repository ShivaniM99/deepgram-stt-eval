"""Level normalization and silence trimming for base clips.

LEVEL CONFOUND -- the problem this exists to fix
------------------------------------------------
As first built, the tiers sat at very different levels: synthesized speech
around -17 dBFS, LibriSpeech around -25, and phone recordings around -43. A
26 dB spread across tiers makes the most important comparison in the project
uninterpretable. If real recordings score worse than synthetic ones, is that
because real speech is harder, or because it arrived 26 dB quieter? Every base
clip is normalized to a common RMS before any channel degradation, so the only
difference between tiers is the speech itself.

Normalization targets RMS rather than peak: a single plosive or stray transient
sets the peak, and peak-normalizing would leave the actual voice inconsistently
quiet across clips.

Trimming is deliberately conservative. An earlier version tried to detect and
excise the recorder keypress from already-captured audio; it worked on some
clips and not others, because once a transient sits within ~250 ms of speech no
threshold separates them without risking the first phoneme. That was the wrong
place to solve it -- the recorder now discards the keypress window at capture
time instead (see scripts/record_tier_c.py --discard-head-ms), which removes
the artifact rather than guessing at it. What remains here only trims silence
that is unambiguously silence.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

TARGET_RMS_DBFS = -23.0
PEAK_CEILING = 0.89          # headroom so mu-law / Opus do not clip
FRAME_MS = 20
PAD_MS = 200                 # silence kept either side of the speech body
MIN_RUN_MS = 60              # shorter activity than this is a blip, not speech
MERGE_GAP_MS = 150           # bridge pauses shorter than this


def _frames(x: np.ndarray, sr: int) -> tuple[np.ndarray, int]:
    n = int(sr * FRAME_MS / 1000)
    f = len(x) // n
    if f == 0:
        return np.array([]), n
    return 20 * np.log10(np.sqrt((x[:f * n].reshape(f, n) ** 2).mean(axis=1)) + 1e-12), n


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    out, start = [], None
    for i, v in enumerate(mask):
        if v and start is None:
            start = i
        elif not v and start is not None:
            out.append((start, i))
            start = None
    if start is not None:
        out.append((start, len(mask)))
    return out


def _clean_runs(runs, min_len: int, merge_gap: int):
    """Drop blips, then bridge short gaps -- ordinary VAD tidy-up. Without it,
    breath and room tone cross the threshold a frame at a time."""
    runs = [(a, b) for a, b in runs if (b - a) >= min_len]
    if not runs:
        return []
    merged = [list(runs[0])]
    for a, b in runs[1:]:
        if a - merged[-1][1] <= merge_gap:
            merged[-1][1] = b
        else:
            merged.append([a, b])
    return [(a, b) for a, b in merged]


def trim_silence(x: np.ndarray, sr: int) -> tuple[np.ndarray, dict]:
    """Trim leading/trailing silence, keeping PAD_MS either side."""
    db, n = _frames(x, sr)
    if db.size == 0:
        return x, {"trimmed": False, "reason": "too short to analyze"}

    noise = float(np.percentile(db, 10))
    loud = float(np.percentile(db, 90))
    thresh = max(noise + 12.0, loud - 30.0)
    runs = _clean_runs(_runs(db > thresh),
                       min_len=max(1, int(MIN_RUN_MS / FRAME_MS)),
                       merge_gap=max(1, int(MERGE_GAP_MS / FRAME_MS)))
    if not runs:
        return x, {"trimmed": False, "reason": "no active audio found"}

    pad = int(PAD_MS / FRAME_MS)
    first = max(0, runs[0][0] - pad)
    last = min(len(db), runs[-1][1] + pad)
    return x[first * n: last * n], {
        "trimmed": first > 0 or last < len(db),
        "lead_removed_ms": first * FRAME_MS,
        "tail_removed_ms": (len(db) - last) * FRAME_MS,
        "noise_floor_dbfs": round(noise, 1),
    }


def normalize_rms(x: np.ndarray, target_dbfs: float = TARGET_RMS_DBFS
                  ) -> tuple[np.ndarray, dict]:
    rms = float(np.sqrt(np.mean(x**2))) + 1e-12
    gain = (10 ** (target_dbfs / 20.0)) / rms
    y = x * gain
    peak = float(np.abs(y).max()) if y.size else 0.0
    limited = False
    if peak > PEAK_CEILING:
        y = y * (PEAK_CEILING / peak)
        limited = True
    return y, {
        "gain_db": round(20 * np.log10(gain), 1),
        "rms_before_dbfs": round(20 * np.log10(rms), 1),
        "rms_after_dbfs": round(20 * np.log10(float(np.sqrt(np.mean(y**2))) + 1e-12), 1),
        "peak_limited": limited,
    }


def trim_trailing_burst(x: np.ndarray, sr: int) -> tuple[np.ndarray, dict]:
    """Remove an isolated burst at the very end -- the stop keypress.

    Unlike the leading keypress, this one is tractable: the speaker is asked to
    leave a beat of silence before pressing Enter, so a real gap separates the
    click from the last word. The cut therefore lands in silence rather than
    being a fixed guess.

    It is adaptive on purpose. A fixed trailing cut would be unsafe here: the
    end of these utterances is where the prescription number falls, so blindly
    discarding the last 300 ms risks the final digit -- exactly the data the
    evaluation is about. If no isolated trailing burst is found, nothing is
    removed and the QC check flags the clip instead.
    """
    db, n = _frames(x, sr)
    if db.size == 0:
        return x, {"tail_burst_removed": False}

    noise = float(np.percentile(db, 10))
    loud = float(np.percentile(db, 90))
    thresh = max(noise + 12.0, loud - 30.0)
    runs = _clean_runs(_runs(db > thresh),
                       min_len=max(1, int(MIN_RUN_MS / FRAME_MS)),
                       merge_gap=max(1, int(MERGE_GAP_MS / FRAME_MS)))
    if len(runs) < 2:
        return x, {"tail_burst_removed": False}

    a, b = runs[-1]
    prev_end = runs[-2][1]
    gap_ms = (a - prev_end) * FRAME_MS
    len_ms = (b - a) * FRAME_MS
    ends_near_end = (len(db) - b) * FRAME_MS <= 300

    if len_ms <= 400 and gap_ms >= 150 and ends_near_end:
        cut = int((prev_end + (a - prev_end) / 2))      # mid-gap, in silence
        return x[: cut * n], {
            "tail_burst_removed": True,
            "tail_removed_ms": (len(db) - cut) * FRAME_MS,
            "gap_before_burst_ms": gap_ms,
        }
    return x, {"tail_burst_removed": False}


def tail_transient_db(path: Path, window_ms: int = 300) -> float:
    """How loud the final window is relative to the body. Positive means the
    clip ends with something louder than the speech -- a stop keypress."""
    x, sr = sf.read(str(path), dtype="float32", always_2d=False)
    if x.ndim > 1:
        x = x.mean(axis=1)
    k = int(sr * window_ms / 1000)
    if len(x) <= k:
        return 0.0
    tail_peak = 20 * np.log10(float(np.abs(x[-k:]).max()) + 1e-12)
    body = 20 * np.log10(float(np.percentile(np.abs(x), 95)) + 1e-12)
    return round(tail_peak - body, 1)


def head_transient_db(path: Path, window_ms: int = 200) -> float:
    """How loud the first window_ms is, relative to the body of the clip.

    A positive value means the clip opens with something louder than the
    speech that follows -- typically a recorder keypress. Used by the QC check
    to flag clips worth re-recording rather than to silently alter them.
    """
    x, sr = sf.read(str(path), dtype="float32", always_2d=False)
    if x.ndim > 1:
        x = x.mean(axis=1)
    k = int(sr * window_ms / 1000)
    if len(x) <= k:
        return 0.0
    head_peak = 20 * np.log10(float(np.abs(x[:k]).max()) + 1e-12)
    body = 20 * np.log10(float(np.percentile(np.abs(x), 95)) + 1e-12)
    return round(head_peak - body, 1)


def prepare(src: Path, dst: Path, do_trim: bool = True,
            target_dbfs: float = TARGET_RMS_DBFS, force: bool = False) -> dict:
    """Trim silence then level-normalize. Idempotent via dst existence."""
    if dst.exists() and not force:
        return {"skipped": True}
    x, sr = sf.read(str(src), dtype="float32", always_2d=False)
    if x.ndim > 1:
        x = x.mean(axis=1)
    info: dict = {"src": src.name, "sr": sr, "dur_before_s": round(len(x) / sr, 3)}
    if do_trim:
        x, tinfo = trim_silence(x, sr)
        info.update(tinfo)
    x, ninfo = normalize_rms(x, target_dbfs)
    info.update(ninfo)
    info["dur_after_s"] = round(len(x) / sr, 3)
    sf.write(str(dst), np.clip(x, -1.0, 1.0), sr, subtype="PCM_16")
    return info
