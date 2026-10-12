"""Content-addressed cache for Deepgram responses.

Keyed on (audio bytes, request config), so re-running the same experiment costs
nothing and is byte-for-byte reproducible. This is the single most useful piece
of plumbing in the harness: scoring logic gets iterated on dozens of times
while the underlying API responses are paid for exactly once.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .config import CACHE


def audio_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def config_hash(cfg: dict[str, Any]) -> str:
    canonical = json.dumps(cfg, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def _slot(ahash: str, chash: str) -> Path:
    d = CACHE / ahash[:2]
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{ahash}.{chash}.json"


def get(ahash: str, cfg: dict[str, Any]) -> dict | None:
    p = _slot(ahash, config_hash(cfg))
    if p.exists():
        return json.loads(p.read_text())
    return None


def put(ahash: str, cfg: dict[str, Any], response: dict) -> None:
    _slot(ahash, config_hash(cfg)).write_text(json.dumps(response))


def stats() -> dict[str, int]:
    files = list(CACHE.rglob("*.json"))
    return {
        "entries": len(files),
        "bytes": sum(f.stat().st_size for f in files),
    }
