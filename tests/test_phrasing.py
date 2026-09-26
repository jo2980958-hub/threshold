import pytest

from threshold import phrasing


def test_assert_safe_passes_the_allowed_strings():
    for text in (
        phrasing.ARRIVAL_RECORDED,
        phrasing.NO_ARRIVAL_RECORDED,
        phrasing.NOT_ENOUGH_HISTORY,
        phrasing.BOUNDARY_NOTE,
        phrasing.NO_MORNING_ACTIVITY_YET,
    ):
        assert phrasing.assert_safe(text) == text


@pytest.mark.parametrize(
    "bad_text",
    [
        "The carer did not come today.",
        "Your carer didn't show up.",
        "No-show recorded for the 8am visit.",
        "The visit was a no show.",
        "This looks like fraud.",
        "The carer faked the visit.",
        "She was not here during the scheduled window.",
    ],
)
def test_assert_safe_rejects_accusatory_phrasing(bad_text):
    with pytest.raises(ValueError):
        phrasing.assert_safe(bad_text)


def test_no_arrival_recorded_string_itself_contains_no_forbidden_phrase():
    # Guards the exact literal, so a future edit to the constant can't
    # accidentally reintroduce an accusation without failing this test.
    assert phrasing.assert_safe(phrasing.NO_ARRIVAL_RECORDED) == phrasing.NO_ARRIVAL_RECORDED
    lowered = phrasing.NO_ARRIVAL_RECORDED.lower()
    assert "carer" not in lowered
    assert "did not come" not in lowered
    assert "no show" not in lowered


def test_forbidden_substrings_are_all_lowercase_for_case_insensitive_matching():
    for phrase in phrasing.FORBIDDEN_SUBSTRINGS:
        assert phrase == phrase.lower()


# --- the model-output gate -------------------------------------------
#
# phrasing.py is the single choke point for every judge-facing string,
# including anything a model produces. These assert the stricter gate that
# narrate.py runs Bedrock's output through.

ALLOWED = {"0", "1", "2", "5", "7", "15", "2026", "9", "14", "20"}


def test_model_gate_passes_a_sentence_that_only_describes_the_door():
    text = "The doors recorded 15 events between 2026-09-14 and 2026-09-20."

    assert phrasing.assert_model_output_safe(text, ALLOWED) == text


@pytest.mark.parametrize(
    "draft",
    [
        "The carer did not come on Wednesday.",
        "She was out all morning.",
        "He rang the bell twice.",
        "Nobody was recorded at the door.",
        "They recorded nothing on Friday.",
        "The nurse was there for 2 visits.",
        "The cleaner arrived late.",
        "Their morning was quiet.",
    ],
)
def test_model_gate_rejects_anything_that_puts_a_person_at_the_door(draft):
    with pytest.raises(phrasing.PhrasingViolation):
        phrasing.assert_model_output_safe(draft, ALLOWED)


@pytest.mark.parametrize(
    "draft",
    [
        "This is cause for concern.",
        "The record proves the window was empty.",
        "It seems to have been a quiet week.",
        "We recommend contacting the agency.",
        "This may indicate a change.",
        "It is probably nothing.",
    ],
)
def test_model_gate_rejects_a_conclusion_however_softly_worded(draft):
    with pytest.raises(phrasing.PhrasingViolation):
        phrasing.assert_model_output_safe(draft, ALLOWED)


def test_model_gate_rejects_a_number_the_rules_never_computed():
    with pytest.raises(phrasing.PhrasingViolation) as excinfo:
        phrasing.assert_model_output_safe("The doors recorded 43 events.", ALLOWED)

    assert "43" in str(excinfo.value)


def test_model_gate_treats_a_zero_padded_number_as_the_same_number():
    # "08" and "8" are the same figure, and a gate that disagreed would reject
    # correct prose about an 08:00 visit.
    assert phrasing.numbers_in("08:05") == {"8", "5"}


def test_model_gate_does_not_fire_on_a_banned_word_hiding_inside_another_word():
    # "other" contains "her", "anytime" contains "any". A blunt substring test
    # would reject this sentence and make the gate look broken rather than strict.
    text = "The other door recorded 2 events."

    assert phrasing.assert_model_output_safe(text, ALLOWED) == text


def test_model_gate_is_case_insensitive():
    with pytest.raises(phrasing.PhrasingViolation):
        phrasing.assert_model_output_safe("THE CARER DID NOT COME.", ALLOWED)


def test_the_short_boundary_note_says_the_same_thing_as_the_long_one():
    for text in (phrasing.BOUNDARY_NOTE, phrasing.BOUNDARY_NOTE_SHORT, phrasing.EXPORT_HEADER):
        phrasing.assert_safe(text)
        lowered = text.lower()
        assert "not" in lowered
        assert "who" in lowered or "missing" in lowered


def test_phrasing_violation_is_a_value_error_so_existing_callers_still_catch_it():
    assert issubclass(phrasing.PhrasingViolation, ValueError)
