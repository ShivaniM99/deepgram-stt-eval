#!/usr/bin/env python
"""Render the launch-readiness report and exit non-zero if a blocker failed.

Non-zero exit is the point: this is a CI gate, not a document. Wiring it into
a pipeline means a model bump that regresses drug-name accuracy fails the
build instead of reaching a pharmacy queue.

  python scripts/04_report.py
  python scripts/04_report.py --no-gate   # render only, always exit 0
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dgeval.gate import evaluate, verdict
from dgeval.report import gate_measurements, load_results, write
from rich.console import Console
from rich.table import Table

console = Console()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-gate", action="store_true",
                    help="render the report but always exit 0")
    args = ap.parse_args()

    data = load_results()
    if not data["arms"]:
        console.print("[yellow]no results found in results/summary.json — "
                      "run scripts/02_run_eval.py first[/]")
        raise SystemExit(2)

    meas = gate_measurements(data)
    context = {k: v for k, v in meas.items() if k.startswith("_")}
    checks = evaluate({k: v for k, v in meas.items() if not k.startswith("_")})
    vd = verdict(checks)

    t = Table(title="launch gate", show_edge=False)
    for c in ("check", "severity", "observed", "bound", "result"):
        t.add_column(c, justify="left" if c == "check" else "right")
    for c in checks:
        if c.skipped:
            res, style = "SKIP", "dim"
        elif c.passed:
            res, style = "PASS", "green"
        else:
            res, style = "FAIL", "red"
        t.add_row(c.name, c.severity,
                  "n/a" if c.observed is None else f"{c.observed:.4g}",
                  c.bound, f"[{style}]{res}[/]")
    console.print(t)

    colour = {"GO": "green", "GO WITH CONDITIONS": "yellow", "NO-GO": "red"}[vd["decision"]]
    for k, v in context.items():
        console.print(f"[dim]{k.lstrip('_')}: {v}[/]")
    console.print(f"\n[bold {colour}]VERDICT: {vd['decision']}[/]")
    if vd["blockers_failed"]:
        console.print(f"  blockers: {', '.join(vd['blockers_failed'])}")
    if vd["warnings"]:
        console.print(f"  warnings: {', '.join(vd['warnings'])}")
    if vd["skipped"]:
        console.print(f"  [dim]not measured: {', '.join(vd['skipped'])}[/]")

    path = write()
    console.print(f"\nreport -> {path}")

    raise SystemExit(0 if args.no_gate else vd["exit_code"])


if __name__ == "__main__":
    main()
