"""Scoring: WER for comparability, entity accuracy for the decision.

The argument this module encodes
--------------------------------
WER treats every word as equally important. On a pharmacy refill line it is
not. A transcript at 12% WER with every drug name, Rx number and date of birth
correct is shippable. A transcript at 4% WER that turns "hydroxyzine" into
"hydralazine" is a dispensing-error pathway -- the same word count, a
completely different outcome. So WER is reported because it is comparable
across vendors and published benchmarks, and entity accuracy is what the
launch gate actually tests.

Three severities are distinguished, which matters more than the headline rate:

  correct          the entity was recognized
  miss             the entity was mangled into something that is not a drug;
                   a pharmacist or a validation layer will catch this
  CROSS-CONFUSION  the entity was recognized as a DIFFERENT REAL DRUG that is
                   a documented ISMP confusable of the true one

The third is the dangerous one, because downstream validation cannot catch it:
the output is a well-formed, plausible, wrong drug. It is counted and reported
separately rather than folded into a generic error rate.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

import jiwer

from .normalize import digits_only, normalize

MONTHS = {m.lower(): i for i, m in enumerate(
    ["", "january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"]) if m}


# --- WER --------------------------------------------------------------------

@dataclass
class WerResult:
    wer: float
    hits: int
    substitutions: int
    deletions: int
    insertions: int
    ref_words: int

    @property
    def total_errors(self) -> int:
        return self.substitutions + self.deletions + self.insertions


def wer(reference: str, hypothesis: str) -> WerResult:
    ref, hyp = normalize(reference), normalize(hypothesis)
    if not ref:
        # No reference words: WER is undefined. Insertions are still counted,
        # which is how tier D fabrication gets measured.
        n_ins = len(hyp.split())
        return WerResult(float("nan"), 0, 0, 0, n_ins, 0)
    out = jiwer.process_words(ref, hyp)
    return WerResult(
        wer=out.wer, hits=out.hits, substitutions=out.substitutions,
        deletions=out.deletions, insertions=out.insertions,
        ref_words=len(ref.split()),
    )


# --- entity scoring ---------------------------------------------------------

@dataclass
class EntityOutcome:
    kind: str
    expected: Any
    found: Any
    status: str            # correct | miss | cross_confusion
    confused_with: str | None = None


def score_drug(expected: str, confusable_with: list[str], hyp: str) -> EntityOutcome:
    h = normalize(hyp)
    toks = set(h.split())
    exp = normalize(expected)
    if exp in toks or exp in h:
        return EntityOutcome("drug", expected, exp, "correct")
    for other in confusable_with or []:
        o = normalize(other)
        if o in toks or o in h:
            return EntityOutcome("drug", expected, o, "cross_confusion", other)
    return EntityOutcome("drug", expected, None, "miss")


def score_rx(expected: str, hyp: str) -> EntityOutcome:
    digits = digits_only(hyp)
    if expected in digits:
        return EntityOutcome("rx", expected, expected, "correct")
    return EntityOutcome("rx", expected, digits or None, "miss")


def _extract_dates(hyp: str) -> list[str]:
    """Pull ISO dates out of a normalized hypothesis.

    Post-normalization a spoken date looks like "march 14 1962"; a
    smart_format numeric date looks like "3 14 1962". Both are handled.
    """
    h = normalize(hyp)
    found: list[str] = []
    month_alt = "|".join(MONTHS)
    for m in re.finditer(rf"\b({month_alt})\s+(\d{{1,2}})\s+(\d{{4}})\b", h):
        found.append(f"{int(m.group(3)):04d}-{MONTHS[m.group(1)]:02d}-{int(m.group(2)):02d}")
    for m in re.finditer(r"\b(\d{1,2})\s+(\d{1,2})\s+(\d{4})\b", h):
        mo, day, yr = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= mo <= 12 and 1 <= day <= 31:
            found.append(f"{yr:04d}-{mo:02d}-{day:02d}")
    return found


def score_dob(expected: str, hyp: str) -> EntityOutcome:
    dates = _extract_dates(hyp)
    if expected in dates:
        return EntityOutcome("dob", expected, expected, "correct")
    return EntityOutcome("dob", expected, dates[0] if dates else None, "miss")


def score_dose(expected: str, hyp: str) -> EntityOutcome:
    e, h = normalize(expected), normalize(hyp)
    return EntityOutcome("dose", expected, e if e in h else None,
                         "correct" if e in h else "miss")


def score_entities(entities: dict[str, Any], hyp: str) -> list[EntityOutcome]:
    out: list[EntityOutcome] = []
    if "drug" in entities:
        out.append(score_drug(entities["drug"],
                              entities.get("drug_confusable_with", []), hyp))
    if "rx" in entities:
        out.append(score_rx(entities["rx"], hyp))
    if "dob" in entities:
        out.append(score_dob(entities["dob"], hyp))
    if "dose" in entities:
        out.append(score_dose(entities["dose"], hyp))
    return out


# --- per-clip and aggregate -------------------------------------------------

@dataclass
class ClipScore:
    uid: str
    tier: str
    condition: str
    locale: str | None
    reference: str
    hypothesis: str
    wer: WerResult
    entities: list[EntityOutcome] = field(default_factory=list)
    probe: str | None = None

    def as_row(self) -> dict[str, Any]:
        ent = {f"{e.kind}_status": e.status for e in self.entities}
        return {
            "uid": self.uid, "tier": self.tier, "condition": self.condition,
            "locale": self.locale, "wer": self.wer.wer,
            "sub": self.wer.substitutions, "del": self.wer.deletions,
            "ins": self.wer.insertions, "ref_words": self.wer.ref_words,
            "probe": self.probe,
            "hyp_empty": not normalize(self.hypothesis),
            **ent,
        }


def score_clip(row: dict, hypothesis: str) -> ClipScore:
    return ClipScore(
        uid=row["uid"], tier=row["tier"], condition=row.get("condition", "source"),
        locale=row.get("locale"), reference=row.get("text", ""),
        hypothesis=hypothesis, wer=wer(row.get("text", ""), hypothesis),
        entities=score_entities(row.get("entities") or {}, hypothesis),
        probe=row.get("probe"),
    )


def entity_rates(scores: list[ClipScore]) -> dict[str, dict[str, Any]]:
    """Per-entity-kind accuracy, with cross-confusion broken out."""
    buckets: dict[str, Counter] = defaultdict(Counter)
    for s in scores:
        for e in s.entities:
            buckets[e.kind][e.status] += 1
    out: dict[str, dict[str, Any]] = {}
    for kind, c in buckets.items():
        n = sum(c.values())
        out[kind] = {
            "n": n,
            "correct": c["correct"],
            "miss": c["miss"],
            "cross_confusion": c["cross_confusion"],
            "accuracy": c["correct"] / n if n else float("nan"),
            "cross_confusion_rate": c["cross_confusion"] / n if n else 0.0,
        }
    return out


def confusion_pairs(scores: list[ClipScore]) -> list[dict[str, Any]]:
    """Which ISMP pairs actually collapsed, ranked by frequency."""
    c: Counter = Counter()
    for s in scores:
        for e in s.entities:
            if e.kind == "drug" and e.status == "cross_confusion":
                c[(str(e.expected), str(e.confused_with))] += 1
    return [{"expected": a, "heard_as": b, "count": n}
            for (a, b), n in c.most_common()]


def corpus_wer(scores: list[ClipScore]) -> float:
    """Aggregate WER computed over pooled counts, not as a mean of per-clip WERs.

    Averaging per-clip rates over-weights short clips: a one-word clip with a
    single error contributes 100% to the mean. Pooling errors over total
    reference words is the standard definition and the only one comparable to
    a published benchmark figure.
    """
    errs = sum(s.wer.total_errors for s in scores if s.wer.ref_words)
    words = sum(s.wer.ref_words for s in scores if s.wer.ref_words)
    return errs / words if words else float("nan")


def hallucination_report(scores: list[ClipScore]) -> dict[str, Any]:
    """Fabrication on non-speech input.

    Any word at all on a clip containing no speech is an insertion. The
    per-probe breakdown matters more than the total: silence and hold music
    fail for different reasons, and a system that is clean on digital silence
    but invents sentences over music has a specific, fixable defect.
    """
    d = [s for s in scores if s.tier == "D_nonspeech"]
    by_probe: dict[str, dict[str, Any]] = {}
    for probe in sorted({s.probe for s in d if s.probe}):
        sub = [s for s in d if s.probe == probe]
        fab = [s for s in sub if normalize(s.hypothesis)]
        by_probe[probe] = {
            "n": len(sub),
            "fabricated": len(fab),
            "rate": len(fab) / len(sub) if sub else 0.0,
            "words_total": sum(len(normalize(s.hypothesis).split()) for s in sub),
            "examples": [s.hypothesis for s in fab[:3]],
        }
    n_fab = sum(v["fabricated"] for v in by_probe.values())
    return {
        "n_clips": len(d),
        "n_fabricated": n_fab,
        "insertion_rate": n_fab / len(d) if d else 0.0,
        "by_probe": by_probe,
    }


def slice_table(scores: list[ClipScore], by: str) -> list[dict[str, Any]]:
    """Aggregate by an arbitrary field -- condition, locale or tier."""
    groups: dict[Any, list[ClipScore]] = defaultdict(list)
    for s in scores:
        groups[getattr(s, by)].append(s)
    rows = []
    for key in sorted(groups, key=lambda k: (k is None, k)):
        sub = groups[key]
        er = entity_rates(sub)
        rows.append({
            by: key, "clips": len(sub), "wer": corpus_wer(sub),
            "drug_accuracy": er.get("drug", {}).get("accuracy"),
            "drug_cross_confusion": er.get("drug", {}).get("cross_confusion_rate"),
            "rx_accuracy": er.get("rx", {}).get("accuracy"),
            "dob_accuracy": er.get("dob", {}).get("accuracy"),
        })
    return rows
