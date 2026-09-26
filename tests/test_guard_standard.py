"""The four defects this guard's own design notes reproduced here, pinned.

Each test in this file was written from a reproduction run at a terminal, not
from reading the guard. Every one of them failed before the fix. They are kept
because the interesting property of a guard is not that it holds today; it is
that a later refactor cannot quietly widen it again.

1. §2 -- a spelling variant reached a different comparison than the plain form.
2. §3 -- a number was grounded because it was small, and later, because it was
   written as a word rather than as digits.
3. §3 -- the grounding allowlist was built from a block that contains a name the
   household typed, so a household called "Flat 43" grounded 43.
4. §5 -- the guard raised out of `narrate.summarise`, documented never to raise.
"""

from __future__ import annotations

from datetime import date

import pytest

from threshold import digest as digest_mod
from threshold import narrate, phrasing

GROUNDED = {"0", "4", "7", "8", "15", "2026"}


def _digest(name: str = "Oakfield Road", **kwargs) -> digest_mod.WeeklyDigest:
    fields = dict(
        household_name=name,
        week_start=date(2026, 9, 14),
        week_end=date(2026, 9, 20),
        door_count=2,
        total_events=15,
        days=(),
        visits_scheduled=4,
        visits_with_activity=4,
        visits_without_recorded_arrival=0,
        visits_not_comparable=0,
        quiet_days=(),
        busiest_day=None,
        earliest_activity_hour=None,
        latest_activity_hour=None,
    )
    fields.update(kwargs)
    return digest_mod.WeeklyDigest(**fields)


# --- §2: every spelling reaches the same comparison ----------------------

VARIANTS = [
    ("plain", lambda s: s),
    ("curly", lambda s: s.replace("'", "’")),
    ("spaced", lambda s: s.replace("'", "' ")),
    ("zero-width", lambda s: s.replace(" ", "​ ", 1)),
    ("lookalike", lambda s: s.replace("c", "с", 1)),
    ("double-space", lambda s: s.replace(" ", "  ")),
    ("full-width", lambda s: s.replace("a", "ａ")),
    ("upper", lambda s: s.upper()),
    ("hyphenated", lambda s: s.replace("er ", "er- ", 1)),
]

BLOCKED = [
    "The carer did not come on Wednesday.",
    "She's not recorded at the front door at 8.",
    "The carer's booked time shows nothing at the door.",
    "The courier was not recorded at the door.",
]


@pytest.mark.parametrize("name,mutate", VARIANTS, ids=[v[0] for v in VARIANTS])
@pytest.mark.parametrize("sentence", BLOCKED)
def test_the_gate_sees_through_the_spelling(sentence, name, mutate):
    with pytest.raises(phrasing.PhrasingViolation):
        phrasing.assert_model_output_safe(mutate(sentence), GROUNDED)


def test_a_letter_outside_the_alphabet_is_itself_a_refusal():
    """A fold table can never be finished. `dıd` with a dotless Turkish ı walks
    through every version of one, and so will the next lookalike. The allowlist
    -- anything outside a-z after folding -- is the check that does not need
    extending."""
    exc = _refused("The visits dıd not have any recorded activity.")
    assert exc.rule == "It used letters outside the alphabet the rules are written in."
    assert "dıd" not in exc.rule


def _refused(text: str, allowed: set[str] | None = None) -> phrasing.PhrasingViolation:
    with pytest.raises(phrasing.PhrasingViolation) as excinfo:
        phrasing.assert_model_output_safe(text, GROUNDED if allowed is None else allowed)
    return excinfo.value


# --- §3: a number is grounded by its source, in any spelling -------------

@pytest.mark.parametrize(
    "text",
    [
        "Front-door activity was recorded during five of the booked visits.",
        "Activity was recorded on twenty-three occasions this week.",
        "The third booked visit has no arrival recorded at the front door.",
        "The doors recorded one thousand events.",
        "Activity was recorded once at the front door.",
        "Activity was recorded at the door twice on Monday.",
        "Half of the booked visits recorded activity at the door.",
    ],
)
def test_a_number_written_as_a_word_is_still_a_number(text):
    """The grounding check used to read digit runs only, so every figure spelled
    out walked past it. "five of the booked visits" is the same fabricated
    finding as "5 of the booked visits" and arrives more naturally."""
    assert _refused(text).rule == "It contained a number the rules did not compute."


@pytest.mark.parametrize(
    ("candidate", "source"),
    [
        ("2.50 events", "the doors recorded 2.5 events"),
        ("the 3rd of the month", "activity on the third of the month"),
        ("5 events", "five events were recorded"),
        ("08:15", "activity at 8 and at 15 minutes past"),
    ],
)
def test_the_same_quantity_spelled_differently_is_grounded(candidate, source):
    assert phrasing.numbers_in(candidate) <= phrasing.numbers_in(source)


def test_a_thousands_separator_is_one_figure_and_not_two_small_ones():
    assert phrasing.numbers_in("1,000 events") == {"1000"}


# --- §3: nothing user-supplied may widen the allowlist -------------------

def test_a_household_name_does_not_ground_a_number():
    """`allowed_numbers` used to be `numbers_in(facts_block())`, and the facts
    block opens with a name the household typed on the settings page. A flat
    called "Flat 43" put 43 into the anti-hallucination allowlist for every
    sentence the model wrote about that household."""
    d = _digest("Flat 43")
    assert "43" not in d.allowed_numbers()
    with pytest.raises(phrasing.PhrasingViolation):
        phrasing.assert_model_output_safe("The doors recorded 43 events.", d.allowed_numbers())


def test_the_model_is_still_shown_the_household_name():
    """The fix is to stop the name widening the allowlist, not to stop the model
    seeing it. A summary that cannot say which house it is about is worse prose
    and no safer."""
    assert "Flat 43" in _digest("Flat 43").facts_block()


def test_the_figures_the_rules_computed_are_still_grounded():
    """Non-vacuity: the allowlist has to contain something, or the test above
    passes because the gate refuses everything."""
    allowed = _digest().allowed_numbers()
    assert {"15", "4", "7", "2026"} <= allowed


# --- §5: the guard does not raise out of a function that cannot raise ----

@pytest.mark.parametrize(
    "name",
    ["No-Show Lane", "Faked Row", "The Fraud Arms", "Lied About Cottage", "Did Not Come House"],
)
def test_a_household_name_that_trips_a_rule_does_not_break_the_page(name):
    """`summarise` is documented "Never raises", and callers render its result
    without a try block. A household on No-Show Lane used to get a 500 with the
    developer message -- which quotes the offending sentence -- in the body."""
    summary = narrate.summarise(_digest(name), client=object())
    assert summary.source == narrate.SOURCE_FALLBACK
    assert name in summary.text or summary.text == phrasing.BOUNDARY_NOTE


def test_the_household_name_is_not_censored_when_nothing_else_is_wrong():
    """The mask is on the name, not on the sentence around it. The rest of the
    summary is still checked, and the name still prints."""
    text = digest_mod.deterministic_summary(_digest("No-Show Lane"))
    assert "No-Show Lane" in text
    assert phrasing.BOUNDARY_NOTE in text


def test_the_rule_around_a_masked_name_is_still_enforced():
    """Non-vacuity for the mask: masking a name must not switch the guard off
    for the sentence that contains it."""
    with pytest.raises(phrasing.PhrasingViolation):
        phrasing.assert_safe(
            "At No-Show Lane the carer did not come.", user_spans=("No-Show Lane",)
        )


# --- §5: applied at the boundary, asserted on what reaches the reader ----

class _AccusingClient:
    """The worst thing the model could say, returned by the real call path."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0

    def converse(self, **kwargs):
        self.calls += 1
        return {"output": {"message": {"content": [{"text": self.text}]}}}


@pytest.mark.parametrize(
    "draft",
    [
        "She's not recorded at the front door at 8.",
        "Front-door activity was recorded during five of the booked visits.",
        "The courier did not attend on Tuesday.",
        "The doors recorded 43 events, which speaks for itself.",
    ],
)
def test_nothing_the_gate_refused_reaches_the_family(draft):
    """The real payload, the real path, and an assertion on the text a family
    member is served -- not on `summary.source`, which a timeout satisfies too."""
    client = _AccusingClient(draft)
    summary = narrate.summarise(_digest(), client=client)

    assert client.calls == narrate.MAX_ATTEMPTS, "the model was never actually called"
    assert summary.text == digest_mod.deterministic_summary(_digest())
    for fragment in ("she", "courier", "five", "43", "speaks for itself", "did not"):
        assert fragment not in summary.text.lower()
    # The refusal names the rule and carries none of the refused draft.
    assert summary.rejected_reason
    for word in draft.lower().split():
        assert word.strip(".,") not in summary.rejected_reason.lower() or word in (
            "the", "at", "a", "of", "it", "was", "not", "on",
        )
