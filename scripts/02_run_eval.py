#!/usr/bin/env python
"""Transcribe the corpus under one or more configurations and score the result.

Writes results/<arm>.jsonl (one scored row per clip) and prints the headline
table. Every response is cached, so re-running is free and the second run of
an arm costs nothing.

  python scripts/02_run_eval.py --arms baseline
  python scripts/02_run_eval.py --arms baseline keyterm_all keyterm_half
  python scripts/02_run_eval.py --arms baseline --limit 20   # cheap smoke run
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dgeval import cache
from dgeval.asr.batch import AsrConfig, transcribe_one
from dgeval.config import (MANIFEST, RESULTS, BudgetGuard, BudgetExceeded,
                           PRICE_PER_MIN, has_api_key)
from dgeval.experiments.keyterm import arms as keyterm_arms
from dgeval.metrics.scorers import (corpus_wer, entity_rates, confusion_pairs,
                                    hallucination_report, score_clip, slice_table)
from rich.console import Console
from rich.progress import (BarColumn, Progress, TextColumn, TimeRemainingColumn)
from rich.table import Table
import httpx

console = Console()


def load_manifest(limit: int | None, tiers: str | None) -> list[dict]:
    rows = [json.loads(l) for l in MANIFEST.read_text().splitlines() if l.strip()]
    if tiers:
        keep = set(tiers.split(","))
        rows = [r for r in rows if r["tier"].split("_")[0] in keep]
    if limit:
        # Keep the tier mix when sampling, so a smoke run still exercises
        # non-speech probes rather than only tier A.
        by_tier: dict[str, list[dict]] = {}
        for r in rows:
            by_tier.setdefault(r["tier"], []).append(r)
        per = max(1, limit // max(1, len(by_tier)))
        rows = [r for t in sorted(by_tier) for r in by_tier[t][:per]]
    return rows


def run_arm(name: str, cfg: AsrConfig, rows: list[dict], guard: BudgetGuard):
    scores, cached_n, failed = [], 0, []
    with httpx.Client(timeout=120.0) as client, Progress(
        TextColumn("[bold]{task.description}"), BarColumn(),
        TextColumn("{task.completed}/{task.total}"), TimeRemainingColumn(),
        console=console,
    ) as prog:
        task = prog.add_task(f"{name}", total=len(rows))
        for row in rows:
            try:
                res = transcribe_one(Path(row["path"]), cfg, guard,
                                     row["duration_s"], client=client)
            except BudgetExceeded:
                console.print("[red]budget ceiling reached, stopping this arm[/]")
                break
            except Exception as exc:                      # noqa: BLE001
                failed.append((row["uid"], f"{type(exc).__name__}: {exc}"))
                prog.advance(task)
                continue
            cached_n += res.from_cache
            scores.append(score_clip(row, res.transcript))
            prog.advance(task)
    return scores, cached_n, failed


def print_summary(name: str, scores) -> dict:
    domain = [s for s in scores if s.tier == "A_domain_synthetic"]
    control = [s for s in scores if s.tier == "B_real_control"]
    real = [s for s in scores if s.tier == "C_real_domain"]
    er = entity_rates(domain)
    hr = hallucination_report(scores)

    t = Table(title=f"{name}: headline", show_edge=False)
    t.add_column("metric"); t.add_column("value", justify="right")
    t.add_column("note", style="dim")
    def row(k, v, note=""):
        t.add_row(k, v, note)
    row("WER (tier A, all conditions)", f"{corpus_wer(domain):.2%}" if domain else "-",
        "pooled over reference words")
    if control:
        row("WER (tier B control)", f"{corpus_wer(control):.2%}",
            "LibriSpeech; calibrates the harness")
    if real:
        row("WER (tier C real voice)", f"{corpus_wer(real):.2%}", "your recordings")
    if "drug" in er:
        row("drug accuracy", f"{er['drug']['accuracy']:.2%}", f"n={er['drug']['n']}")
        row("drug CROSS-CONFUSION", f"{er['drug']['cross_confusion_rate']:.2%}",
            f"{er['drug']['cross_confusion']} clips -- heard a different real drug")
    for k in ("rx", "dob", "dose"):
        if k in er:
            row(f"{k} accuracy", f"{er[k]['accuracy']:.2%}", f"n={er[k]['n']}")
    row("hallucination insertion rate", f"{hr['insertion_rate']:.2%}",
        f"{hr['n_fabricated']}/{hr['n_clips']} non-speech clips")
    console.print(t)

    if domain:
        ct = Table(title=f"{name}: by channel condition", show_edge=False)
        for c in ("condition", "clips", "WER", "drug acc", "cross-conf", "rx acc"):
            ct.add_column(c, justify="left" if c == "condition" else "right")
        for r in slice_table(domain, "condition"):
            f = lambda v: "-" if v is None else f"{v:.1%}"
            ct.add_row(str(r["condition"]), str(r["clips"]), f(r["wer"]),
                       f(r["drug_accuracy"]), f(r["drug_cross_confusion"]),
                       f(r["rx_accuracy"]))
        console.print(ct)

        lt = Table(title=f"{name}: by accent", show_edge=False)
        for c in ("locale", "clips", "WER", "drug acc", "cross-conf"):
            lt.add_column(c, justify="left" if c == "locale" else "right")
        for r in slice_table(domain, "locale"):
            f = lambda v: "-" if v is None else f"{v:.1%}"
            lt.add_row(str(r["locale"]), str(r["clips"]), f(r["wer"]),
                       f(r["drug_accuracy"]), f(r["drug_cross_confusion"]))
        console.print(lt)

    cps = confusion_pairs(domain)
    if cps:
        console.print("[bold red]cross-confusions observed[/] "
                      "(a different REAL drug was transcribed):")
        for c in cps[:12]:
            console.print(f"   {c['expected']} -> [red]{c['heard_as']}[/]  x{c['count']}")
    elif domain:
        console.print("[green]no cross-confusions observed[/]")

    return {
        "arm": name,
        "wer_domain": corpus_wer(domain) if domain else None,
        "wer_control": corpus_wer(control) if control else None,
        "wer_real": corpus_wer(real) if real else None,
        "entities": er,
        "hallucination": hr,
        "confusions": cps,
        "by_condition": slice_table(domain, "condition") if domain else [],
        "by_locale": slice_table(domain, "locale") if domain else [],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arms", nargs="+", default=["baseline"])
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--tiers", default=None, help="e.g. A,B,D")
    ap.add_argument("--model", default="nova-3")
    args = ap.parse_args()

    if not has_api_key():
        console.print("[red]DEEPGRAM_API_KEY not set.[/] "
                      "Copy .env.example to .env and add your key.\n"
                      "Everything up to this point (corpus build, scoring logic, "
                      "gate) runs without it -- see `make test`.")
        raise SystemExit(2)

    rows = load_manifest(args.limit, args.tiers)
    available = keyterm_arms(AsrConfig(model=args.model))
    guard = BudgetGuard.from_env()

    mins = sum(r["duration_s"] for r in rows) / 60
    console.print(f"{len(rows)} clips, {mins:.1f} min of audio per arm, "
                  f"{len(args.arms)} arm(s)")
    console.print(f"worst case if nothing is cached: "
                  f"${mins * len(args.arms) * PRICE_PER_MIN['nova-3-prerecorded']:.2f}  "
                  f"(guard ceiling {guard.max_minutes:.0f} min)")

    summaries = []
    for name in args.arms:
        if name not in available:
            raise SystemExit(f"unknown arm {name!r}; available: {sorted(available)}")
        scores, cached_n, failed = run_arm(name, available[name], rows, guard)
        console.print(f"[dim]{cached_n}/{len(scores)} from cache, "
                      f"{len(failed)} failed[/]")
        for uid, err in failed[:5]:
            console.print(f"  [yellow]{uid}: {err}[/]")
        summaries.append(print_summary(name, scores))
        (RESULTS / f"{name}.jsonl").write_text(
            "".join(json.dumps({**s.as_row(), "hypothesis": s.hypothesis,
                                "reference": s.reference}) + "\n" for s in scores))

    (RESULTS / "summary.json").write_text(json.dumps(summaries, indent=2, default=str))
    console.print(f"\nbillable this run: {guard.spent_minutes:.1f} min "
                  f"= ${guard.estimated_usd():.3f}   "
                  f"cache now holds {cache.stats()['entries']} responses")
    console.print(f"results -> {RESULTS}")


if __name__ == "__main__":
    main()
