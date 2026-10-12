#!/usr/bin/env python
"""Verify the Deepgram key works, and report the balance. Costs nothing.

Does three things, in increasing order of commitment:
  1. confirms a key is present in .env
  2. lists projects (an authenticated GET -- no audio, no charge)
  3. reports remaining credit, so spend is visible before a run rather than after

Never prints the key itself.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import httpx

from dgeval.config import api_key, has_api_key
from rich.console import Console

console = Console()
BASE = "https://api.deepgram.com/v1"


def main() -> None:
    if not has_api_key():
        console.print("[red]No key found.[/] Expected DEEPGRAM_API_KEY in "
                      f"{Path('.env').resolve()}")
        console.print("  cp .env.example .env   then paste your key after the '='")
        raise SystemExit(2)

    key = api_key()
    console.print(f"[dim]key present: {len(key)} chars, "
                  f"starts {key[:4]}... (not logged in full)[/]")
    h = {"Authorization": f"Token {key}"}

    with httpx.Client(timeout=20.0, headers=h) as c:
        r = c.get(f"{BASE}/projects")
        if r.status_code == 401:
            console.print("[red]401 Unauthorized.[/] The key was rejected. "
                          "Check for a stray space or a truncated paste, or "
                          "create a fresh key in the console.")
            raise SystemExit(1)
        r.raise_for_status()
        projects = r.json().get("projects", [])
        console.print(f"[green]authenticated[/] — {len(projects)} project(s)")

        for p in projects:
            pid, name = p.get("project_id"), p.get("name", "unnamed")
            console.print(f"  [bold]{name}[/]")
            try:
                b = c.get(f"{BASE}/projects/{pid}/balances")
                b.raise_for_status()
                for bal in b.json().get("balances", []):
                    amt = bal.get("amount")
                    units = bal.get("units", "")
                    console.print(f"    balance: {amt} {units}")
                    if isinstance(amt, (int, float)) and amt < 5:
                        console.print("    [yellow]under $5 remaining — a full "
                                      "run of this harness costs about $1.30[/]")
            except httpx.HTTPError:
                console.print("    [dim]balance not readable with this key "
                              "(needs an admin-scoped key); not a problem[/]")

    console.print("\n[green]key works.[/] Next, the cheap smoke test:")
    console.print("  .venv/bin/python scripts/02_run_eval.py --arms baseline --limit 8")
    console.print("[dim]8 clips, a few cents. It exercises the real request "
                  "parameters for the first time, so a wrong parameter name "
                  "fails there instead of 640 clips in.[/]")


if __name__ == "__main__":
    main()
