"""Numeric grounding: is this figure one the source actually stated?

Split from `matching.py` on the seam between two different jobs: folding a
string so a list can be compared against it, and deciding whether a number is
allowed. They share the fold and nothing else, and the rule below is that
second job in one sentence:

> **A number is allowed because it appears in the source. Never because it is
> small, common, or plausible.**

Threshold's `allowed |= {str(n) for n in range(0, 8)}` is that failure in one
line. Every integer 0-7 was grounded unconditionally, and every number this
product argues about -- visits scheduled, visits without arrival, quiet days,
door count, days covered -- lives in that range. The anti-hallucination check
was inoperative for exactly the figures an accusation rides on.

Digits are not the only way to write a number, so the allowlist is built from
the source with every spelling folded to one canonical form, and the candidate
is folded the same way. If a figure is hard to ground, that is a signal the
field wants a stronger shape from §1, not a reason to widen the allowlist.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from .matching import normalise

_UNITS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
          "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
          "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
          "seventeen": 17, "eighteen": 18, "nineteen": 19}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
         "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
_ORD = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6,
        "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10, "once": 1, "twice": 2,
        "half": Decimal("0.5")}

# A digit run, optionally extended by thousands-separator groups. Each group is
# exactly one separator character immediately followed by exactly three digits
# that are not themselves followed by a fourth -- `(?!\d)` is what enforces
# "exactly three", the same job `\b` cannot do once `_` is one of the
# separators, because `_` is a word character and a digit-to-`_` transition is
# never a `\b` boundary.
#
# This used to be a single greedy `\d[\d,_ ]*` blob with the three-ness checked
# only afterwards, as a best-effort cleanup. That let a comma-space list
# separator between two *unrelated* numbers -- "2026-09-21, 2026-09-22", which
# a model writes constantly when a fact block hands it several dates -- get
# swallowed into one match, because a space followed by four digits is still
# "[\d,_ ]*". The cleanup step could not tell "the thousands part of one
# number" from "the start of the next number" after the fact, so it stripped
# the separator either way and produced "212026": a number nobody computed,
# out of two numbers everybody did. See
# `tests/test_guard_standard.py::test_a_thousands_separator_is_one_figure_and_not_two_small_ones`
# for the case this still has to get right.
_NUM_RE = re.compile(r"\d+(?:[,_ ]\d{3}(?!\d))*(?:\.\d+)?|\.\d+")
_CLOCK_RE = re.compile(r"\b(\d{1,2}):(\d{2})\b")


def canon(value) -> str:
    """One spelling per quantity: 2.50, 2.5 and "two point five" all give "2.5"."""
    try:
        d = Decimal(str(value)).normalize()
    except InvalidOperation:
        return ""
    if d == d.to_integral_value():
        d = d.quantize(Decimal(1))
    return format(d, "f")


def _digit_numbers(text: str) -> set[str]:
    # No follow-up cleanup regex needed: `_NUM_RE` itself now only ever
    # includes a separator when it sits between two genuine three-digit
    # groups of the same number, so every comma/underscore/space inside a
    # match is safe to drop unconditionally.
    out = set()
    for m in _NUM_RE.finditer(text):
        cleaned = m.group(0).replace(",", "").replace("_", "").replace(" ", "")
        if c := canon(cleaned):
            out.add(c)
    return out


def _word_numbers(text: str) -> set[str]:
    out: set[str] = set()
    toks = re.findall(r"[a-z]+", text)
    i = 0
    while i < len(toks):
        t = toks[i]
        if t in _UNITS or t in _TENS:
            val = Decimal(_UNITS.get(t, _TENS.get(t, 0)))
            j = i + 1
            if t in _TENS and j < len(toks) and toks[j] in _UNITS:
                val += _UNITS[toks[j]]
                j += 1
            if j < len(toks) and toks[j] == "point":
                frac, k = "", j + 1
                while k < len(toks) and toks[k] in _UNITS and _UNITS[toks[k]] < 10:
                    frac += str(_UNITS[toks[k]])
                    k += 1
                if frac:
                    val, j = Decimal(f"{val}.{frac}"), k
            if j < len(toks) and toks[j] in ("hundred", "thousand"):
                val *= 100 if toks[j] == "hundred" else 1000
                j += 1
            out.add(canon(val))
            i = j
            continue
        if t in _ORD:
            out.add(canon(_ORD[t]))
        i += 1
    return out


def numbers_in(text: str) -> set[str]:
    """Every quantity `text` states, however it is spelled.

    Digits, digits with separators, decimals, word numbers and their compounds,
    ordinals in both forms, `once`/`twice`/`half`, and both halves of a clock
    time. Unicode digits are handled by the NFKC step in `normalise`.
    """
    t = normalise(text)                      # hyphens folded, so "twenty-three" splits
    found = _digit_numbers(t) | _word_numbers(t)
    for m in _CLOCK_RE.finditer(t):
        found |= {canon(m.group(1)), canon(m.group(2))}
    return found


def ungrounded_numbers(candidate: str, source: str) -> list[str]:
    """Every number in the candidate the source does not contain. Empty == grounded."""
    allowed = numbers_in(source)
    return sorted(n for n in numbers_in(candidate) if n not in allowed)
