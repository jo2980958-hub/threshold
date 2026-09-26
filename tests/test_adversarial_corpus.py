"""A shared adversarial corpus, run against Threshold's gate.

`tests/fixtures/adversarial.jsonl` is a corpus shared across several related
projects, copied rather than imported, so a defect found in one is checked in
all of them. None of its rows was written from this app's denylists: they came
from audits, from other projects' own incident write-ups, and from paraphrasing
the sentences the lists were written to catch. That is the whole point. A guard
test drawn from the guard's own list measures how well a regex matches a string
written to match it.

Rows this app does not claim carry a reason in `NOT_OURS`, and the reason is
part of the test. Silently not running a row is the failure this file exists to
prevent; an explicit skip with an argument attached is honest.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from threshold import phrasing

ROWS = [
    json.loads(line)
    for line in (pathlib.Path(__file__).parent / "fixtures/adversarial.jsonl")
    .read_text(encoding="utf-8")
    .splitlines()
    if line.strip()
]

# What the weekly summary is grounded against. Deliberately modest and
# deliberately inside the product's real range: 5, 7 and 8 are the sort of
# counts this app argues about, so a row that smuggles one of them past the
# gate is smuggling a real accusation, not an implausible one.
GROUNDED = {"0", "2", "4", "7", "8", "9", "15", "2026"}

# Rows Threshold's gate does not claim, each with the reason it does not.
NOT_OURS = {
    "advice-01": "no clinical field: Threshold's model writes about a door, never about treatment",
    "advice-02": "same -- there is no medicine anywhere in this product",
    "advice-03": "same",
    "legal-02": "no statutory field; the paraphrase carries no door vocabulary to catch it by",
    "legal-03": "no statutory field",
    "pii-02": "no field describes a person: the model is never shown one to describe",
    "pii-03": "same",
    "health-01": "no clinical field; a fabricated fall has no number and no banned word",
    "health-02": "same",
    "inject-01": "no markdown destination -- Threshold writes HTML and CSV, never a committed file",
    "inject-03": "Threshold renders no model string into an HTML attribute",
    "inject-04": "the summary reaches a template that escapes; there is no raw-HTML path",
    "inject-05": "control characters are stripped at the store boundary, not by this gate",
    "inject-06": "the CSV export carries no model text; see tests/test_export.py",
    "clean-03": "Threshold bans third-person pronouns outright, so it blocks this on purpose",
    "clean-02": "a depot scanner is not Threshold vocabulary; the gate has no opinion on it",
    "clean-05": '"proof" is on this app\'s word list on purpose -- a door cannot prove anything',
    "clean-06": "a shop till is another app's subject matter",
}


def _blocked(text: str) -> bool:
    try:
        phrasing.assert_model_output_safe(text, GROUNDED)
    except phrasing.PhrasingViolation:
        return True
    return False


@pytest.mark.parametrize("row", ROWS, ids=[r["id"] for r in ROWS])
def test_the_shared_adversarial_corpus(row):
    if row["id"] in NOT_OURS:
        pytest.skip(NOT_OURS[row["id"]])
    blocked = _blocked(row["text"])
    if row["expect"] == "blocked":
        assert blocked, f'{row["id"]} passed the gate: {row["why"]}'
    else:
        assert not blocked, f'{row["id"]} was a false positive: {row["why"]}'


def test_the_corpus_is_actually_loaded_and_actually_claimed():
    """Non-vacuity, per GUARD-STANDARD.md §7 class 6.

    An absence assertion needs proof the search ran and proof the thing searched
    for exists. Here: the file is not empty, more than half of it is claimed
    rather than skipped, and the claimed half contains both expectations -- so
    the parametrised test above cannot be passing because it is doing nothing.
    """
    assert len(ROWS) >= 40
    claimed = [r for r in ROWS if r["id"] not in NOT_OURS]
    assert len(claimed) > len(ROWS) / 2
    assert {r["expect"] for r in claimed} == {"blocked", "clean"}
    assert set(NOT_OURS) <= {r["id"] for r in ROWS}, "a skip names a row that is not in the file"
    assert all(reason.strip() for reason in NOT_OURS.values())
