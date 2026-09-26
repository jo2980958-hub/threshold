"""Normalisation for guard matching: one fold, so one comparison.

**Nothing this module returns is ever displayed.** It produces a matching copy:
a deliberately lossy fold of a string, used to compare against a list, and then
thrown away. The text a reader sees is always the caller's original. Offsets, if
a caller ever needs them, are taken against the original too.

It exists to close off the first of a family of matching
mistakes: **matching raw text.** The guard compares a list against bytes the
model chose, so the model chooses the bytes. `she's`, `she’s`, `she' s`, `ѕhe's`
(Cyrillic ѕ) and `s​he's` (zero-width space) must all reach the same comparison,
or the list is decorative.

Two forms come out of `prepare`, because one is not enough:

- `spaced`   — apostrophes and hyphens become spaces: `she's` -> `she s`
- `squeezed` — they vanish: `she's` -> `shes`, `care-r` -> `carer`

Token membership is checked against the union. The `spaced` form is what stops a
contraction hiding a pronoun inside one token; the `squeezed` form is what stops
`care-r` and `did' not` from splitting a word in half. You need both or you have
traded one hole for another.

Numeric grounding -- §3 of the same document -- lives in `grounding.py`, which
imports the fold from here.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# Invisible or rendering-steering. None of them carries meaning for a guard, and
# every one of them is a way to split a banned word in half.
_INVISIBLE = {
    0x00AD, 0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF,   # soft hyphen, zero-width family
    0x202A, 0x202B, 0x202C, 0x202D, 0x202E,           # bidi embedding / override
    0x2066, 0x2067, 0x2068, 0x2069,                   # bidi isolates
}

# Latin lookalikes that spell English words. Deliberately not exhaustive: a fold
# table is a denylist wearing different clothes, and `foreign_letters` below is
# the allowlist that catches everything this map does not.
_LOOKALIKE = {
    "а": "a", "б": "b", "е": "e", "ѕ": "s", "і": "i", "ј": "j", "ӏ": "l", "о": "o",
    "р": "p", "с": "c", "у": "y", "х": "x", "ԁ": "d", "һ": "h", "ԛ": "q", "ѡ": "w",
    "ν": "v", "ο": "o", "ρ": "p", "α": "a", "ε": "e", "ι": "i", "κ": "k", "τ": "t",
}

_PUNCT = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'", "ʼ": "'", "ʻ": "'",
    "´": "'", "`": "'", "′": "'", "＇": "'",
    "“": '"', "”": '"', "„": '"', "″": '"',
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-",
    "―": "-", "−": "-",
    " ": " ", " ": " ", " ": " ", " ": " ", " ": " ",
    " ": " ", " ": " ",
}

_FOLD = str.maketrans({**_PUNCT, **_LOOKALIKE})
_DROP = {cp: None for cp in _INVISIBLE}
_WORD_SPLIT = re.compile(r"[^a-z0-9]+")
_PUNCT_RUN = re.compile(r"['\-]{2,}")
# `she' s`, `she ' s`, `didn 't`: a space either side of an apostrophe inside a
# word is still that word.
_LOOSE_APOSTROPHE = re.compile(r"\s*'\s*")


def normalise(text: str) -> str:
    """Fold a string to the form the denylists are written in.

    The order is load-bearing and it is the order that catches an upper-case
    Cyrillic С: case-fold first, *then* fold lookalikes, because the lookalike
    map is written in lower case and a capital would miss it entirely.
    """
    s = unicodedata.normalize("NFKC", text)
    s = s.translate(_DROP)
    s = "".join(c for c in s if c in "\n\t" or unicodedata.category(c) not in ("Cc", "Cf"))
    s = s.casefold()                                   # before the fold, not after
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = unicodedata.normalize("NFC", s).translate(_FOLD)
    s = _LOOSE_APOSTROPHE.sub("'", s)
    s = _PUNCT_RUN.sub(lambda m: m.group(0)[0], s)
    s = re.sub(r"[^\S\n]+", " ", s)
    s = re.sub(r" *\n *", "\n", s)
    return s.strip()


@dataclass(frozen=True)
class Normalised:
    """A matching copy. Never render any field of this."""

    original: str
    spaced: str      # "she's" -> "she s"
    squeezed: str    # "she's" -> "shes"
    tokens: frozenset[str]

    def has_word(self, word: str) -> bool:
        n = normalise(word)
        return n in self.tokens or re.sub(r"['-]", "", n) in self.tokens

    def has_phrase(self, phrase: str) -> bool:
        n = normalise(phrase)
        spaced = re.sub(r"\s+", " ", re.sub(r"['-]", " ", n)).strip()
        return spaced in self.spaced or re.sub(r"['-]", "", n) in self.squeezed

    def foreign_letters(self) -> list[str]:
        """Letters that survived folding. Non-empty means somebody is trying.

        `dıd not` with a dotless Turkish ı walks through the lookalike map, and
        so will the next lookalike, and the one after. The map cannot be
        finished. This can: for an English-only guarded field, a letter outside
        `a-z` after folding is either a language the guard was never written for
        or somebody probing it, and both are a refusal.
        """
        return sorted({c for c in self.squeezed if c.isalpha() and not ("a" <= c <= "z")})


def prepare(text: str) -> Normalised:
    n = normalise(text)
    spaced = re.sub(r"\s+", " ", re.sub(r"\s*['-]\s*", " ", n)).strip()
    squeezed = re.sub(r"\s*['-]\s*", "", n)
    tokens = frozenset(t for t in _WORD_SPLIT.split(spaced + " " + squeezed) if t)
    return Normalised(text, spaced, squeezed, tokens)
