"""Paths, credentials and the spend guard.

Everything that touches money or the filesystem resolves through here so there
is exactly one place to audit before a run.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

DATA = ROOT / "data"
CORPUS = DATA / "corpus"
AUDIO = CORPUS / "audio"
MANIFEST = CORPUS / "manifest.jsonl"
RECORDINGS = DATA / "recordings"
EXTERNAL = DATA / "external"
CACHE = ROOT / ".cache"
REPORTS = ROOT / "reports"
CONFIGS = ROOT / "configs"
RESULTS = ROOT / "results"

for _d in (AUDIO, RECORDINGS, EXTERNAL, CACHE, REPORTS, RESULTS):
    _d.mkdir(parents=True, exist_ok=True)

# Published Deepgram list prices, Pay-As-You-Go, USD. Used only to estimate
# spend for the budget guard and the report's cost model -- never to bill.
PRICE_PER_MIN = {
    "nova-3-prerecorded": 0.0043,
    "nova-3-prerecorded-multi": 0.0052,
    "nova-3-streaming": 0.29 / 60,
    "voice-agent-standard": 0.075,
}


def api_key() -> str:
    key = os.getenv("DEEPGRAM_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "DEEPGRAM_API_KEY is not set. Copy .env.example to .env and add your key "
            "from https://console.deepgram.com (new accounts get $200 free credit)."
        )
    return key


def has_api_key() -> bool:
    return bool(os.getenv("DEEPGRAM_API_KEY", "").strip())


@dataclass
class BudgetGuard:
    """Aborts a run before it can exceed a billable-minutes ceiling.

    Deepgram bills pre-recorded STT per minute of audio and the Voice Agent API
    per minute of *socket connection time*. Both are cheap per unit and easy to
    overspend by accident -- a retry loop over a large corpus, or a hung
    websocket left open during debugging. This makes the ceiling explicit and
    enforced rather than a line in the README.
    """

    max_minutes: float
    spent_minutes: float = 0.0

    @classmethod
    def from_env(cls) -> "BudgetGuard":
        return cls(max_minutes=float(os.getenv("DGEVAL_MAX_BILLABLE_MINUTES", "600")))

    def charge(self, minutes: float, label: str = "nova-3-prerecorded") -> None:
        if self.spent_minutes + minutes > self.max_minutes:
            raise BudgetExceeded(
                f"Run would use {self.spent_minutes + minutes:.1f} billable minutes, "
                f"ceiling is {self.max_minutes:.1f}. Raise DGEVAL_MAX_BILLABLE_MINUTES "
                f"in .env if this is intended."
            )
        self.spent_minutes += minutes

    def estimated_usd(self, label: str = "nova-3-prerecorded") -> float:
        return self.spent_minutes * PRICE_PER_MIN.get(label, 0.0)


class BudgetExceeded(RuntimeError):
    pass
