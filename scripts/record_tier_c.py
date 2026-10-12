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


def record(device: str, dst: Path, discard_head_ms: int = 700) -> bool:
    """Record until the user presses Enter. Returns False if nothing captured.

    The first `discard_head_ms` are thrown away, because the keypress that
    starts the recording is captured by the microphone as a sharp transient --
    on the first pass through this corpus it landed up to 10 dB LOUDER than the
    speech that followed. Detecting and excising it afterwards was tried and
    abandoned: once a transient sits within ~250 ms of speech, no threshold
    separates the two without risking the first phoneme. Discarding a fixed
    window at capture, with an on-screen cue so the speaker waits for it,
    removes the artifact instead of guessing at it.
    """
    tmp = dst.with_suffix(".rec.wav")
    proc = subprocess.Popen(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-f", "avfoundation", "-i", f":{device}",
         "-ac", "1", "-ar", "16000", "-acodec", "pcm_s16le", str(tmp)],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    # Hold the speaker off until the discard window has certainly elapsed.
    # The margin covers ffmpeg's audio-device init: if capture starts slightly
    # after Popen, the discarded window shifts later in wall-clock time, and a
    # cue shown too early would let a fast speaker land inside it.
    time.sleep(discard_head_ms / 1000.0 + 0.4)
    console.print("  [bold red]SPEAK NOW[/] — press [bold]Enter[/] when you finish the line")
    try:
        input()
    except (KeyboardInterrupt, EOFError):
        pass
    err_bytes = b""
    try:
        # 'q' makes ffmpeg finalize the container cleanly; terminate would truncate.
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

    # Drop the keypress window.
    x, sr = sf.read(str(tmp), dtype="float32", always_2d=False)
    if x.ndim > 1:
        x = x.mean(axis=1)
    k = int(sr * discard_head_ms / 1000.0)
    if len(x) > k + sr // 4:
        x = x[k:]
    # The stop keypress is only sometimes captured -- ffmpeg often finalizes
    # before it lands. Remove it when it IS there, adaptively, because the end
    # of these utterances carries the prescription number.
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
    ap.add_argument("--discard-head-ms", type=int, default=700,
                    help="audio thrown away at the start to drop the keypress")
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
        "Read each line [bold]naturally[/]. Do not over-enunciate the drug names —\n"
        "mumbled is realistic and useful. Read digits [bold]one at a time[/]\n"
        "(\"six two six\", never \"six hundred twenty-six\"). Quiet room,\n"
        "phone held normally, no headset.",
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
            if not record(args.device, wav, args.discard_head_ms):
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
