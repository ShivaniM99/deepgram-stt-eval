"""Normalizer tests.

Each case is a (spoken reference, plausible Deepgram output) pair that MUST
land on the same normalized string. These encode the methodology claim in the
README -- if one breaks, a reported WER in the report is wrong.
"""
from dgeval.metrics.normalize import normalize, digits_only

EQUIVALENT = [
    ("four four eight two one zero nine", "4482109"),
    ("prescription number three eight eight three one nine eight", "Rx number 3883198"),
    ("fifty milligrams", "50 mg"),
    ("twenty five milligrams", "25 mgs"),
    ("one hundred micrograms", "100 mcg"),
    ("July second, nineteen fifty four", "July 2nd, 1954"),
    ("date of birth March fourteenth, nineteen sixty two", "DOB March 14, 1962"),
    ("I'd like a refill on hydralazine", "I would like a refill on hydralazine"),
    ("zero point five milligrams", "0.5 mg"),
    ("three point one two five milligrams", "3.125 mg"),
    ("my date of birth is April eighth, nineteen eighty", "my DOB is April 8, 1980"),
]

DISTINCT = [
    # Confusable drug names must NOT normalize together -- the whole point.
    ("hydroxyzine", "hydralazine"),
    ("clonazepam", "clonidine"),
    ("prednisone", "prednisolone"),
    ("metformin", "metronidazole"),
]


def test_equivalent_forms_agree():
    for ref, hyp in EQUIVALENT:
        a, b = normalize(ref), normalize(hyp)
        assert a == b, f"{ref!r} -> {a!r}  !=  {hyp!r} -> {b!r}"


def test_year_reading_is_narrow():
    # "twenty five" is a dose, not the year 2025.
    assert normalize("twenty five milligrams") == "25 milligrams"
    assert normalize("nineteen sixty two") == "1962"


def test_confusable_drugs_stay_distinct():
    for a, b in DISTINCT:
        assert normalize(a) != normalize(b), f"{a} and {b} collapsed together"


def test_digits_only_extraction():
    for s in ["four four eight two one zero nine", "4482109", "Rx 448-2109",
              "my number is 4 4 8 2 1 0 9"]:
        assert digits_only(s) == "4482109", f"{s!r} -> {digits_only(s)!r}"


def test_day_and_year_do_not_merge():
    assert normalize("March fourteenth, nineteen sixty two") == "march 14 1962"
