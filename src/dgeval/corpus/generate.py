"""Template-driven utterance generation with exact entity labels.

The core trick of this harness: utterances are *composed* from templates with
entity slots, so the ground-truth transcript and the structured entity labels
fall out of the same step. Nothing is hand-annotated, which is what makes a
few hundred exactly-labelled domain clips feasible at all.

Spoken forms are written out as words ("four four eight two one zero nine")
rather than digits, for two reasons:
  1. TTS engines read a bare "4482109" as a single large cardinal number,
     which is not how a caller reads a bottle label.
  2. It forces the scoring side to normalize explicitly instead of getting a
     free pass from a coincidental string match. See metrics/normalize.py.
"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass, asdict, field
from datetime import date
from typing import Any

from ..config import DATA

DIGIT_WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]

ORDINALS = [
    "", "first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth",
    "ninth", "tenth", "eleventh", "twelfth", "thirteenth", "fourteenth", "fifteenth",
    "sixteenth", "seventeenth", "eighteenth", "nineteenth", "twentieth",
    "twenty first", "twenty second", "twenty third", "twenty fourth", "twenty fifth",
    "twenty sixth", "twenty seventh", "twenty eighth", "twenty ninth", "thirtieth",
    "thirty first",
]

MONTHS = ["", "January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]

TENS = {2: "twenty", 3: "thirty", 4: "forty", 5: "fifty", 6: "sixty", 7: "seventy",
        8: "eighty", 9: "ninety"}

UNDER_20 = ["", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
            "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
            "seventeen", "eighteen", "nineteen"]


def digits_spoken(s: str) -> str:
    """'4482109' -> 'four four eight two one zero nine' (how a caller reads it)."""
    return " ".join(DIGIT_WORDS[int(c)] for c in s if c.isdigit())


def _two_digit_words(n: int) -> str:
    if n < 20:
        return UNDER_20[n]
    tens, unit = divmod(n, 10)
    return TENS[tens] + (f" {UNDER_20[unit]}" if unit else "")


def year_spoken(year: int) -> str:
    """1962 -> 'nineteen sixty two'. Restricted to 1900-1999 by the DOB sampler."""
    return f"{_two_digit_words(year // 100)} {_two_digit_words(year % 100)}"


def dob_spoken(d: date) -> str:
    return f"{MONTHS[d.month]} {ORDINALS[d.day]}, {year_spoken(d.year)}"


@dataclass
class Utterance:
    uid: str
    template_id: str
    text: str                      # exactly what is spoken == the WER reference
    entities: dict[str, Any]       # canonical values, for entity-level scoring
    tier: str = "A_domain_synthetic"
    voice: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)


def _load(name: str) -> dict:
    return json.loads((DATA / name).read_text())


def generate(n: int = 100, seed: int = 20261012) -> list[Utterance]:
    rng = random.Random(seed)
    drugs = _load("drugs.json")["drugs"]
    tpl_data = _load("templates.json")
    templates = tpl_data["templates"]
    names = tpl_data["names"]

    # Weight drug sampling so every confusable pair is exercised, rather than
    # letting uniform sampling leave half the pairs with one or zero examples.
    out: list[Utterance] = []
    for i in range(n):
        tpl = templates[i % len(templates)]
        drug = drugs[i % len(drugs)]
        ents: dict[str, Any] = {}
        fills: dict[str, str] = {}

        if "drug" in tpl["slots"]:
            fills["drug"] = drug["name"]
            ents["drug"] = drug["name"]
            ents["drug_confusable_with"] = drug["confusable_with"]
        if "dose" in tpl["slots"]:
            dose = rng.choice(drug["doses"])
            fills["dose"] = dose
            ents["dose"] = dose
        if "rx" in tpl["slots"]:
            rx = str(rng.randint(1, 9)) + "".join(str(rng.randint(0, 9)) for _ in range(6))
            fills["rx"] = digits_spoken(rx)
            ents["rx"] = rx
        if "dob" in tpl["slots"]:
            d = date(rng.randint(1940, 1999), rng.randint(1, 12), rng.randint(1, 28))
            fills["dob"] = dob_spoken(d)
            ents["dob"] = d.isoformat()
        if "name" in tpl["slots"]:
            nm = rng.choice(names)
            fills["name"] = nm
            ents["name"] = nm

        out.append(
            Utterance(
                uid=f"A{i:04d}",
                template_id=tpl["id"],
                text=tpl["text"].format(**fills),
                entities=ents,
            )
        )
    return out


def coverage(utts: list[Utterance]) -> dict[str, Any]:
    """Sanity report -- catches a corpus that silently under-covers a slot."""
    from collections import Counter
    drugs = Counter(u.entities.get("drug") for u in utts if "drug" in u.entities)
    return {
        "utterances": len(utts),
        "distinct_drugs": len([d for d in drugs if d]),
        "with_rx": sum(1 for u in utts if "rx" in u.entities),
        "with_dob": sum(1 for u in utts if "dob" in u.entities),
        "with_dose": sum(1 for u in utts if "dose" in u.entities),
        "templates_used": len({u.template_id for u in utts}),
    }
