"""Threshold evaluation: turn measurements into a launch decision.

Returns a non-zero exit code when a blocker fails, so this runs in CI on every
model or config change rather than being a report somebody read once.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import yaml

from .config import CONFIGS


@dataclass
class Check:
    name: str
    severity: str
    passed: bool
    observed: float | None
    bound: str
    why: str
    skipped: bool = False
    note: str = ""


def load(path: str | None = None) -> dict:
    return yaml.safe_load((CONFIGS / "gate.yaml" if path is None else path).read_text()
                          if path is None else open(path).read())


def _fmt(v: float | None) -> str:
    return "n/a" if v is None else f"{v:.4g}"


def evaluate(measurements: dict[str, float | None], spec: dict | None = None) -> list[Check]:
    """measurements maps threshold name -> observed value (None if not measured)."""
    spec = spec or load()
    checks: list[Check] = []
    for name, rule in spec["thresholds"].items():
        why = " ".join(str(rule.get("why", "")).split())
        sev = rule.get("severity", "warn")
        obs = measurements.get(name)

        if obs is None:
            checks.append(Check(name, sev, True, None, "-", why, skipped=True,
                                note="not measured in this run"))
            continue

        if "max" in rule:
            bound, passed = f"<= {rule['max']}", obs <= rule["max"]
        elif "min" in rule:
            bound, passed = f">= {rule['min']}", obs >= rule["min"]
        elif "max_delta" in rule:
            bound, passed = f"delta <= {rule['max_delta']}", obs <= rule["max_delta"]
        else:
            checks.append(Check(name, sev, True, obs, "-", why, skipped=True,
                                note="rule has no bound"))
            continue

        checks.append(Check(name, sev, passed, obs, bound, why))
    return checks


def verdict(checks: list[Check]) -> dict[str, Any]:
    active = [c for c in checks if not c.skipped]
    blockers = [c for c in active if c.severity == "blocker" and not c.passed]
    warns = [c for c in active if c.severity == "warn" and not c.passed]
    skipped = [c for c in checks if c.skipped]
    if blockers:
        decision = "NO-GO"
    elif warns:
        decision = "GO WITH CONDITIONS"
    else:
        decision = "GO"
    return {
        "decision": decision,
        "blockers_failed": [c.name for c in blockers],
        "warnings": [c.name for c in warns],
        "skipped": [c.name for c in skipped],
        "n_evaluated": len(active),
        "exit_code": 1 if blockers else 0,
    }
