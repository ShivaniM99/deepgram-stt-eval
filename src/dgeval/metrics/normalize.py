"""Text normalization for WER -- stated explicitly, because it moves the number.

Normalization choice is the most common way a reported WER becomes
unfalsifiable. Deepgram's `smart_format` emits "4482109" where a caller said
"four four eight two one zero nine"; scored naively against a word-form
reference that is seven substitutions, and the same system looks either
excellent or broken depending on a flag nobody wrote down.

This module maps BOTH reference and hypothesis into one space. Every rule it
applies is listed here:

  1. lowercase, unify apostrophes
  2. decimal numerals -> spoken form:  "0.5"  -> "0 point 5"
     numeric ordinals lose their suffix:  "14th" -> "14"
  3. expand contractions against a fixed map ("i'd" -> "i would")
  4. split on sentence punctuation into SEGMENTS; number runs are collapsed
     only within a segment, never across one. This matters: "March fourteenth,
     nineteen sixty two" must not merge the day into the year.
  5. expand unit abbreviations (mg -> milligrams, mcg -> micrograms)
  6. ordinals -> cardinals ("fourteenth" -> "fourteen")
  7. collapse runs of number words into numerals, three cases:
       - all single digits      "four four eight"     -> "448"
       - century + remainder    "nineteen sixty two"  -> "1962"
       - cardinal arithmetic    "twenty five"         -> "25"

Rule 7's second case is the load-bearing one and it is deliberately narrow:
only a leading 10-19 triggers the year reading, so "twenty five milligrams"
stays 25 rather than becoming 2025. A spoken year and a spoken 4-digit
cardinal are genuinely ambiguous in English and no normalizer resolves both.

That residual ambiguity is exactly why WER is treated here as the blunt
comparability metric, and why the launch decision rests on per-entity accuracy
instead -- where parsing is exact rather than heuristic. See scorers.py.
"""
from __future__ import annotations

import re

_UNITS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
          "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
          "seventeen", "eighteen", "nineteen"]
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
         "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}

_WORD2NUM: dict[str, int] = {w: i for i, w in enumerate(_UNITS)}
_WORD2NUM.update(_TENS)
_SINGLE_DIGITS = set(_UNITS[:10])
_NUMWORDS = set(_WORD2NUM) | {"hundred", "thousand"}

_ORDINAL2CARD = {
    "first": "one", "second": "two", "third": "three", "fifth": "five",
    "eighth": "eight", "ninth": "nine", "twelfth": "twelve",
}

_CONTRACTIONS = {
    "i'd": "i would", "i'll": "i will", "i'm": "i am", "i've": "i have",
    "it's": "it is", "that's": "that is", "there's": "there is",
    "what's": "what is", "let's": "let us", "he's": "he is", "she's": "she is",
    "don't": "do not", "doesn't": "does not", "didn't": "did not",
    "can't": "cannot", "won't": "will not", "isn't": "is not",
    "aren't": "are not", "wasn't": "was not", "couldn't": "could not",
    "wouldn't": "would not", "shouldn't": "should not", "haven't": "have not",
    "you're": "you are", "we're": "we are", "they're": "they are",
    "you'll": "you will", "we'll": "we will", "you've": "you have",
}

_ABBREV = {
    "mg": "milligrams", "mgs": "milligrams", "milligram": "milligrams",
    "mcg": "micrograms", "microgram": "micrograms", "ug": "micrograms",
    "ml": "milliliters", "milliliter": "milliliters",
    "g": "grams", "gram": "grams",
    "rx": "prescription", "dob": "date of birth",
}

_SEGMENT_SPLIT = re.compile(r"[,;.!?:—–]|\s-\s")


def _ordinal_to_cardinal(tok: str) -> str:
    if tok in _ORDINAL2CARD:
        return _ORDINAL2CARD[tok]
    if tok.endswith("ieth") and tok[:-4] + "y" in _TENS:
        return tok[:-4] + "y"
    if tok.endswith("th") and tok[:-2] in _WORD2NUM:
        return tok[:-2]
    return tok


def _run_to_numeral(run: list[str]) -> str:
    if not run:
        return ""

    # Case A: every token is a single digit -> a digit string, as read off a label.
    if all(t in _SINGLE_DIGITS for t in run):
        return "".join(str(_WORD2NUM[t]) for t in run)

    # Case B: century + remainder -> a year. Narrow on purpose: only a leading
    # 10-19, so "twenty five" is not misread as 2025.
    if len(run) >= 2 and run[0] in _WORD2NUM and 10 <= _WORD2NUM[run[0]] <= 19:
        lead, rest, val = _WORD2NUM[run[0]], run[1:], 0
        for t in rest:
            if t not in _WORD2NUM:
                val = -1
                break
            val = val + _WORD2NUM[t] if (val and val % 10 == 0) else _WORD2NUM[t]
        if 0 <= val <= 99:
            return f"{lead}{val:02d}"

    # Case C: ordinary cardinal arithmetic.
    total, current = 0, 0
    for t in run:
        if t in _WORD2NUM:
            current += _WORD2NUM[t]
        elif t == "hundred":
            current = (current or 1) * 100
        elif t == "thousand":
            total += (current or 1) * 1000
            current = 0
        else:
            return " ".join(run)
    return str(total + current)


def _normalize_segment(seg: str) -> str:
    seg = re.sub(r"[^\w\s'-]", " ", seg)
    toks = [x for x in re.split(r"\s+", seg) if x]
    toks = [y for x in toks for y in _ABBREV.get(x, x).split()]
    toks = [_ordinal_to_cardinal(x) for x in toks]

    out: list[str] = []
    run: list[str] = []
    for tok in toks:
        if tok in _NUMWORDS:
            run.append(tok)
        else:
            if run:
                out.append(_run_to_numeral(run))
                run = []
            out.append(tok)
    if run:
        out.append(_run_to_numeral(run))
    return " ".join(x for x in out if x)


def normalize(text: str) -> str:
    if not text:
        return ""
    t = text.lower().replace("’", "'")
    t = re.sub(r"(\d)\.(\d)", r"\1 point \2", t)          # 0.5 -> 0 point 5
    t = re.sub(r"\b(\d+)(st|nd|rd|th)\b", r"\1", t)        # 14th -> 14 (smart_format)
    for k, v in _CONTRACTIONS.items():
        t = re.sub(rf"\b{re.escape(k)}\b", v, t)
    t = re.sub(r"\b(\w+)'s\b", r"\1s", t)                  # possessives
    segments = [_normalize_segment(s) for s in _SEGMENT_SPLIT.split(t)]
    return re.sub(r"\s+", " ", " ".join(s for s in segments if s)).strip()


def digits_only(text: str) -> str:
    """Entity-grade extraction for Rx numbers: normalize, then keep digits."""
    return re.sub(r"\D", "", normalize(text))
