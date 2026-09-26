"""Tests the gate against text the denylists do not contain.

`test_phrasing.py` feeds the guard the entries in its own lists and asserts they
are refused. That proves the list contains the words in the list. It cannot
detect the failure that matters, which is a sentence a model would plausibly
write that the list was not written to catch.

The bug that prompted this file:

    "She's not recorded at the front door at 8."

passed every check. `she` is on the word list, but the word matcher split on
``[a-z']+`` and the apostrophe in `she's` is a different character depending on
whether the model, an editor or a word processor produced it — and even the
straight one left `she's` as a token that never compared equal to `she`.
Separately, every number from 0 to 7 was grounded unconditionally on the
grounds that a small count cannot assert anything, so "no arrival was recorded
during 6 of the visits" was a fabricated finding about somebody's care that
passed the anti-hallucination gate because 6 is small.

So: contractions, unicode, spacing, homoglyphs, paraphrase, and numbers the
digest never produced. Each case is one a real model output could take.
"""

from __future__ import annotations

from datetime import date

import pytest

from threshold import digest as digest_mod
from threshold import phrasing

GROUNDED = {"2", "5", "7", "15", "2026", "9", "14", "20"}


def _refused(text: str, allowed: set[str] | None = None) -> phrasing.PhrasingViolation:
    with pytest.raises(phrasing.PhrasingViolation) as excinfo:
        phrasing.assert_model_output_safe(text, allowed if allowed is not None else GROUNDED)
    return excinfo.value


# --- contractions and the apostrophes they are written with ---------------

@pytest.mark.parametrize(
    "text",
    [
        "She's not recorded at the front door at 2.",          # the reported bug
        "She’s not recorded at the front door at 2.",     # curly apostrophe
        "Sheʼs not recorded at the front door at 2.",     # modifier letter
        "she' s not recorded at the front door",               # spaced apostrophe
        "She  's not recorded",                                 # and doubled space
        "He'd have been recorded.",
        "They're not in the record.",
        "Nobody's activity appears on Friday.",
        "Their arrival isn't in the log.",
    ],
)
def test_a_contraction_does_not_smuggle_a_banned_word_through(text):
    assert _refused(text).rule == "It used a word the rules do not allow about people."


@pytest.mark.parametrize(
    "text",
    [
        "The carer didn’t come on Tuesday.",              # curly
        "The carer didnʼt come on Tuesday.",
        "It was a no‑show.",                               # non-breaking hyphen
        "It was a no–show.",                               # en dash
        "The visit was missed the visit",                  # non-breaking space
    ],
)
def test_a_punctuation_variant_does_not_smuggle_a_banned_phrase_through(text):
    assert _refused(text).rule == "It used a phrase from the refused-language list."


def test_a_homoglyph_does_not_walk_past_the_list():
    """Cyrillic a in `carer`. This is how a denylist gets evaded on purpose
    rather than by accident, and normalising costs one translate table."""
    assert _refused("The cаrer did not come.")


def test_zero_width_characters_between_letters_do_not_help():
    assert _refused("The ca​rer did not come.".replace("​", ""))
    assert _refused("S​he was at the door.".replace("​", ""))


# --- numeric grounding ----------------------------------------------------

@pytest.mark.parametrize("n", ["0", "1", "3", "4", "6", "8", "43"])
def test_a_small_number_is_not_grounded_just_for_being_small(n):
    """The old rule admitted 0 to 7 unconditionally. "No arrival was recorded
    during 6 of the visits" is a fabricated finding about a household's care,
    and 6 is small. Size is not grounding; the facts block is."""
    text = f"Front-door activity was recorded during {n} of the booked visits."
    assert _refused(text, {"2", "5", "15"}).rule == (
        "It contained a number the rules did not compute."
    )


def test_numbers_the_rules_did_compute_still_pass():
    text = "The doors recorded 15 events across 7 days, and 2 days recorded nothing."
    assert phrasing.assert_model_output_safe(text, GROUNDED) == text


def test_the_week_length_is_grounded_because_the_digest_states_it():
    """`7` used to pass because it was under 8. It now passes because the facts
    block says how many days the record covers, which is a real computation."""
    d = digest_mod.WeeklyDigest(
        household_name="Oakfield Road",
        week_start=date(2026, 9, 14),
        week_end=date(2026, 9, 20),
        door_count=2,
        days=[],
        total_events=0,
        visits_scheduled=0,
        visits_with_activity=0,
        visits_without_recorded_arrival=0,
        visits_not_comparable=0,
        quiet_days=[],
        busiest_day=None,
        earliest_activity_hour=None,
        latest_activity_hour=None,
    )
    assert "Days covered by this record: 7" in d.facts_block()
    assert "7" in d.allowed_numbers()


# --- paraphrase the list was never written to hold ------------------------

@pytest.mark.parametrize(
    "text",
    [
        "Her arrival is absent from Thursday's record.",
        "The cleaner appears twice this week.",
        "Somebody was at the door on Monday.",
        "This probably means the visit was short.",
        "The pattern raises questions about Wednesday.",
        "We recommend contacting the agency.",
    ],
)
def test_paraphrases_that_put_a_person_at_the_door_are_refused(text):
    _refused(text)


def test_a_sentence_about_doors_rather_than_people_is_allowed_through():
    """The gate has to be able to pass something, or it is not a gate, it is a
    wall, and a wall means the model never contributes and the fallback is the
    only path anybody ever exercises."""
    text = (
        "The front door recorded 15 events across 7 days. "
        "On 2 of those days nothing was recorded at the door."
    )
    assert phrasing.assert_model_output_safe(text, GROUNDED) == text


# --- the normaliser itself ------------------------------------------------

@pytest.mark.parametrize(
    ("raw", "folded"),
    [
        ("She’s", "she's"),
        ("she' s", "she's"),
        ("no‑show", "no-show"),
        ("a b", "a b"),
        ("cаrer", "carer"),
        ("  Spaced   out  ", "spaced out"),
    ],
)
def test_normalise_folds_what_the_lists_are_written_against(raw, folded):
    assert phrasing.normalise(raw) == folded


def test_normalise_is_only_ever_used_for_comparison():
    """The text a reader gets is never the folded form: the gates return the
    original string, unchanged, or raise."""
    original = "The front door recorded 15 events across 7 days."
    assert phrasing.assert_model_output_safe(original, GROUNDED) is original
