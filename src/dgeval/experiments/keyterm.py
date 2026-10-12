"""Keyterm prompting: measure the lift AND the cost.

Everyone reports the lift
-------------------------
Turning on Nova-3 keyterm prompting for a list of drug names raises drug-name
recall. That result is uninteresting on its own and it is what a vendor demo
shows.

The question a customer actually needs answered is what the boost costs, and
it has two parts, neither of which a lift number captures:

  FALSE TRIGGER  Does boosting "hydroxyzine" make the model emit it on audio
                 where the caller said something else -- in particular on the
                 OTHER half of the ISMP pair? Boosting a confusable term
                 raises its prior; if both halves of a pair are boosted, the
                 model may resolve ambiguity toward whichever is boosted
                 harder rather than toward what was said. A boost that fixes
                 recall on drug A by stealing probability from drug B has made
                 the system more dangerous, not less, and a recall-only
                 evaluation reports it as an improvement.

  COLLATERAL     Does WER on non-boosted speech get worse? Tier B (LibriSpeech,
                 no pharmacy vocabulary at all) is the control: if general WER
                 degrades when a 50-term pharmacy list is attached, that is a
                 real cost to any caller who is not reciting a drug name.

So this experiment runs three arms and reports all three:

  baseline       no keyterm
  keyterm_all    all 50 drug names boosted
  keyterm_half   only ONE member of each ISMP pair boosted

The third arm is the diagnostic. If drug accuracy rises for the boosted half
while the unboosted half gets WORSE than baseline, the boost is moving
probability mass between confusable terms rather than genuinely improving
acoustic discrimination -- which is exactly the finding a customer must know
before shipping a keyterm list.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ..asr.batch import AsrConfig
from ..config import DATA
from ..metrics.scorers import (ClipScore, confusion_pairs, corpus_wer,
                               entity_rates, score_clip)


def all_drug_names() -> list[str]:
    return [d["name"] for d in json.loads((DATA / "drugs.json").read_text())["drugs"]]


def pair_halves() -> tuple[list[str], list[str]]:
    """Split the drug list so that at most one member of each ISMP pair lands
    in the first group. Returns (boosted_half, withheld_half)."""
    drugs = json.loads((DATA / "drugs.json").read_text())["drugs"]
    boosted: list[str] = []
    withheld: list[str] = []
    claimed: set[str] = set()
    for d in drugs:
        name = d["name"]
        if name in claimed:
            continue
        if any(c in claimed for c in d["confusable_with"]):
            withheld.append(name)
        else:
            boosted.append(name)
        claimed.add(name)
    # Anything whose pair was never seen stays withheld rather than boosted,
    # so the withheld arm is never empty for a pair that exists in the corpus.
    return boosted, withheld


def arms(base: AsrConfig | None = None) -> dict[str, AsrConfig]:
    base = base or AsrConfig()
    boosted, _ = pair_halves()
    return {
        "baseline": base,
        "keyterm_all": AsrConfig(**{**base.__dict__,
                                    "keyterm": tuple(all_drug_names())}),
        "keyterm_half": AsrConfig(**{**base.__dict__,
                                     "keyterm": tuple(boosted)}),
    }


@dataclass
class ArmReport:
    arm: str
    n_clips: int
    wer_domain: float
    wer_control: float
    drug_accuracy: float
    drug_cross_confusion: float
    rx_accuracy: float
    confusions: list[dict[str, Any]]


def summarize(arm: str, scores: list[ClipScore]) -> ArmReport:
    domain = [s for s in scores if s.tier == "A_domain_synthetic"]
    control = [s for s in scores if s.tier == "B_real_control"]
    er = entity_rates(domain)
    return ArmReport(
        arm=arm,
        n_clips=len(scores),
        wer_domain=corpus_wer(domain),
        wer_control=corpus_wer(control) if control else float("nan"),
        drug_accuracy=er.get("drug", {}).get("accuracy", float("nan")),
        drug_cross_confusion=er.get("drug", {}).get("cross_confusion_rate", float("nan")),
        rx_accuracy=er.get("rx", {}).get("accuracy", float("nan")),
        confusions=confusion_pairs(domain),
    )


def false_trigger_analysis(scores_by_arm: dict[str, list[ClipScore]]) -> dict[str, Any]:
    """Split drug accuracy by whether the true drug was in the boosted list.

    The signature of a boost that merely moves probability between confusables:
    boosted-half accuracy improves while withheld-half accuracy drops BELOW
    the baseline for those same clips.
    """
    boosted, withheld = pair_halves()
    bset, wset = set(boosted), set(withheld)

    def split(scores: list[ClipScore]) -> dict[str, Any]:
        groups: dict[str, list] = {"boosted": [], "withheld": []}
        for s in scores:
            for e in s.entities:
                if e.kind != "drug":
                    continue
                if str(e.expected) in bset:
                    groups["boosted"].append(e)
                elif str(e.expected) in wset:
                    groups["withheld"].append(e)
        out = {}
        for k, es in groups.items():
            n = len(es)
            out[k] = {
                "n": n,
                "accuracy": sum(e.status == "correct" for e in es) / n if n else None,
                "cross_confusion_rate": sum(e.status == "cross_confusion" for e in es) / n if n else None,
            }
        return out

    per_arm = {arm: split(sc) for arm, sc in scores_by_arm.items()}

    verdict = None
    if "baseline" in per_arm and "keyterm_half" in per_arm:
        b, k = per_arm["baseline"], per_arm["keyterm_half"]
        try:
            boosted_delta = k["boosted"]["accuracy"] - b["boosted"]["accuracy"]
            withheld_delta = k["withheld"]["accuracy"] - b["withheld"]["accuracy"]
            verdict = {
                "boosted_half_delta": boosted_delta,
                "withheld_half_delta": withheld_delta,
                "probability_stealing": bool(boosted_delta > 0.01 and withheld_delta < -0.01),
            }
        except (TypeError, KeyError):
            verdict = None

    return {"per_arm": per_arm, "verdict": verdict}
