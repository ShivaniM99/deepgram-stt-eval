#!/usr/bin/env python
"""Guided recorder for tier C.

Records straight to the correct filename, which is the whole point. Renaming
16 voice memos by hand and matching them to 16 label files is the single most
likely way to corrupt this dataset: one off-by-one and every clip after it is
scored against the wrong reference, producing a confident and completely wrong
report. Here the filename is never typed by a human.

Resumable -- clips that already exist are skipped unless named explicitly, so
you can stop partway and come back.

  python scripts/record_tier_c.py                 # record whatever is missing
  python scripts/record_tier_c.py --list-devices
  python scripts/record_tier_c.py --device 1      # use the MacBook mic
  python scripts/record_tier_c.py --only c03 c07  # redo specific lines
  python scripts/record_tier_c.py --force         # redo everything

First run will trigger a macOS microphone permission prompt for your terminal.
Allow it, then run the script again.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dgeval.config import RECORDINGS
from dgeval.corpus.prepare import trim_trailing_burst
from rich.console import Console
from rich.panel import Panel

console = Console()

SAY_AS = {
    "hydroxyzine": "hy-DROX-i-zeen", "hydralazine": "hy-DRAL-a-zeen",
    "clonazepam": "kloh-NAZ-e-pam", "clonidine": "KLON-i-deen",
    "prednisone": "PRED-ni-sone", "prednisolone": "pred-NIS-o-lone",
    "metformin": "met-FOR-min", "metronidazole": "met-roh-NYE-da-zole",
    "tramadol": "TRAM-a-dol", "trazodone": "TRAZ-o-done",
    "bupropion": "byoo-PROH-pee-on", "buspirone": "byoo-SPY-rone",
    "amlodipine": "am-LOH-di-peen", "amiodarone": "a-MEE-oh-da-rone",
    "glipizide": "GLIP-i-zide", "glyburide": "GLY-bur-ide",
}


def list_devices() -> None:
    p = subprocess.run(["ffmpeg", "-hide_banner", "-f", "avfoundation",
                        "-list_devices", "true", "-i", ""],
                       capture_output=True, text=True)
    out = p.stderr
    started = False
    for line in out.splitlines():
        if "AVFoundation audio devices" in line:
            started = True
            console.print("[bold]audio input devices[/]")
            continue
        if started and "] [" in line:
            console.print("  " + line.split("] ", 1)[-1])


def record(device: str, dst: Path, discard_head_ms: int = 2000,
           countdown_s: float = 4.0) -> bool:
    """Record with a counted-in start. Returns False if nothing was captured.

    Why a countdown rather than a short fixed discard
    -------------------------------------------------
    The keypress that starts the recording is picked up by the microphone as a
    sharp transient -- on the first pass through this corpus it landed up to
    10 dB LOUDER than the speech. Detecting it afterwards was tried and
    abandoned: once a transient sits within ~250 ms of speech, no threshold
    separates them without risking the first phoneme.

    The second attempt discarded a fixed 700 ms from the start of capture and
    showed the cue 1.1 s in. That ALSO failed, for a subtler reason: ffmpeg's
    audio device needs time to initialize, and over a Continuity iPhone link
    that can exceed a second. Capture therefore begins late, the discarded
    window slides later in wall-clock time with it, and the cut lands inside
    the first word instead of inside the keypress. Five clips came back 1-2.6 s
    short with speech starting at sample zero.

    The fix removes the dependency on init latency rather than estimating it.
    The countdown (default 4 s) is made comfortably LONGER than the discard
    (default 2 s), so for any initialization delay between 0 and 2 s the
    discarded window is guaranteed to contain only the keypress and silence,
    and the speaker's first word is guaranteed to survive. The speaker also
    gets an unambiguous cue to speak against.
    """
    tmp = dst.with_suffix(".rec.wav")
    proc = subprocess.Popen(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-f", "avfoundation", "-i", f":{device}",
         "-ac", "1", "-ar", "16000", "-acodec", "pcm_s16le", str(tmp)],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )

    # Counted-in start. Keep quiet through this; it is all discarded.
    whole = int(countdown_s)
    for k in range(whole, 0, -1):
        console.print(f"  [dim]{k}...[/]")
        time.sleep(1.0)
    time.sleep(max(0.0, countdown_s - whole))
    console.print("  [bold red]SPEAK NOW[/] — press [bold]Enter[/] "
                  "a second after your last word")

    try:
        input()
    except (KeyboardInterrupt, EOFError):
        pass

    err_bytes = b""
    try:
        # 'q' makes ffmpeg finalize the container cleanly; terminate truncates.
        _, err_bytes = proc.communicate(input=b"q", timeout=10)
    except subprocess.TimeoutExpired:
        proc.terminate()
        try:
            _, err_bytes = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    if not tmp.exists() or tmp.stat().st_size < 2000:
        err = (err_bytes or b"").decode(errors="replace")
        console.print(f"  [red]nothing recorded.[/] {err.strip()[:200]}")
        console.print("  [dim]if this is a permissions problem, allow microphone "
                      "access for your terminal in System Settings > Privacy & "
                      "Security > Microphone, then rerun[/]")
        tmp.unlink(missing_ok=True)
        return False

    x, sr = sf.read(str(tmp), dtype="float32", always_2d=False)
    if x.ndim > 1:
        x = x.mean(axis=1)

    # Drop the counted-in head. Safe for any init delay below discard_head_ms.
    k = int(sr * discard_head_ms / 1000.0)
    if len(x) > k + sr // 2:
        x = x[k:]
    else:
        console.print("  [yellow]clip shorter than the discard window; "
                      "keeping it whole[/]")

    # The stop keypress is only sometimes captured. Remove it when present,
    # adaptively, because the end of these utterances carries the Rx number.
    x, tinfo = trim_trailing_burst(x, sr)
    if tinfo.get("tail_burst_removed"):
        console.print(f"  [dim]removed a trailing click "
                      f"({tinfo['tail_removed_ms']} ms)[/]")

    sf.write(str(dst), np.clip(x, -1.0, 1.0), sr, subtype="PCM_16")
    tmp.unlink(missing_ok=True)
    return True


def duration(p: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                        "format=duration", "-of", "csv=p=0", str(p)],
                       capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", default="0",
                    help="avfoundation audio index (default 0; --list-devices to see)")
    ap.add_argument("--only", nargs="*", default=None, help="e.g. --only c03 c07")
    ap.add_argument("--force", action="store_true", help="re-record existing clips")
    ap.add_argument("--list-devices", action="store_true")
    ap.add_argument("--discard-head-ms", type=int, default=2000,
                    help="audio thrown away at the start to drop the keypress")
    ap.add_argument("--countdown", type=float, default=4.0,
                    help="count-in seconds before the speak cue; must exceed "
                         "--discard-head-ms so device init cannot clip speech")
    args = ap.parse_args()

    if args.list_devices:
        list_devices()
        return

    labels = sorted(RECORDINGS.glob("c*.txt"))
    if not labels:
        raise SystemExit("no label files; run scripts/make_tier_c_script.py first")

    todo = []
    for lab in labels:
        cid = lab.stem
        if args.only and cid not in args.only:
            continue
        wav = RECORDINGS / f"{cid}.wav"
        if wav.exists() and not (args.force or args.only):
            continue
        todo.append((cid, lab.read_text().strip(), wav))

    if not todo:
        console.print("[green]all 16 clips already recorded.[/] "
                      "Use --force or --only cNN to redo any.")
        return

    console.print(Panel(
        "Each line is counted in: [bold]3... 2... 1...[/] then [bold red]SPEAK NOW[/].\n"
        "Stay quiet during the count — all of it is discarded.\n\n"
        "Read [bold]naturally[/]. Do not over-enunciate the drug names; mumbled is\n"
        "realistic and useful. Read digits [bold]one at a time[/] (\"six two six\",\n"
        "never \"six hundred twenty-six\"). Leave about a second of silence after\n"
        "your last word before pressing Enter.",
        title=f"tier C — {len(todo)} to record", border_style="cyan"))

    for n, (cid, text, wav) in enumerate(todo, 1):
        console.print(f"\n[bold cyan]{cid}[/]  ({n} of {len(todo)})")
        console.print(Panel(text, border_style="white"))
        hints = [f"{d} = {SAY_AS[d]}" for d in SAY_AS if d in text.lower()]
        if hints:
            console.print(f"  [dim]{'; '.join(hints)}[/]")

        while True:
            console.print("  press [bold]Enter[/] to start  "
                          "([bold]s[/] to skip this line)")
            if input().strip().lower() == "s":
                console.print("  [yellow]skipped[/]")
                break
            if not record(args.device, wav, args.discard_head_ms,
                          args.countdown):
                break
            console.print(f"  captured {duration(wav):.1f}s — playing back")
            subprocess.run(["afplay", str(wav)], check=False)
            console.print("  [bold]Enter[/] = keep   "
                          "[bold]r[/] = redo   [bold]s[/] = skip")
            choice = input().strip().lower()
            if choice == "r":
                continue
            if choice == "s":
                wav.unlink(missing_ok=True)
                console.print("  [yellow]discarded[/]")
            else:
                console.print(f"  [green]saved {wav.name}[/]")
            break

    done = sorted(RECORDINGS.glob("c*.wav"))
    console.print(f"\n[bold]{len(done)}/{len(labels)} recorded[/]")
    missing = [l.stem for l in labels if not (RECORDINGS / f"{l.stem}.wav").exists()]
    if missing:
        console.print(f"  still missing: {', '.join(missing)}")
    else:
        console.print("  next: [bold]make corpus[/] to fold tier C into the manifest")


if __name__ == "__main__":
    main()
