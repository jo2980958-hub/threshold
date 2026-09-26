"""The day written by both sides, and the four rules that keep it honest.

A household note is the one string on the page the rules did not write. The
door cannot see a key safe, a side entrance, or a carer arriving with a family
member, and BOUNDARY_NOTE apologises for exactly that. This feature lets the
people who live there write the missing information in.

The forbidden-phrase list deliberately does not apply to a note: a daughter is
entitled to say "the carer didn't come" about her own mother's house, and an app
that refused her would be censoring its user rather than protecting a worker.
What is guaranteed instead is containment, and these are the tests for it.
"""

from datetime import date, datetime, timezone

import pytest

from threshold import digest as digest_mod
from threshold import narrate, phrasing
from threshold.app import ThresholdApp
from threshold.events import parse_webhook_event
from threshold.schedule import ScheduleRule, parse_clock
from threshold.webhook import sign

import json

ACCUSATION = "The carer did not come today, I waited in all morning."


def _app():
    app = ThresholdApp(webhook_secret="s")
    app.store.upsert_household(
        __import__("threshold.household", fromlist=["Household"]).Household(
            id="oakfield",
            name="Oakfield Road",
            doors=(
                __import__("threshold.household", fromlist=["Door"]).Door(
                    "front_door_1", "oakfield", "Front door", is_primary=True
                ),
            ),
        )
    )
    app.store.add_schedule_rule(
        "oakfield",
        ScheduleRule(
            label="Midday check-in",
            days_of_week=(0, 1, 2, 3, 4),
            start_time=parse_clock("12:00"),
            end_time=parse_clock("12:30"),
            active_from=date(2026, 9, 1),
        ),
    )
    payload = {
        "request_id": "evt-1",
        "device_id": "front_door_1",
        "event_type": "motion_detected",
        "created_at": "2026-09-14T08:05:00Z",
        "attributes": {"sub_type": "human"},
    }
    raw = json.dumps(payload).encode("utf-8")
    app.handle_webhook(raw, sign("s", raw))
    return app


# --- rule 1: always a quotation, always attributed ---------------------


def test_a_note_is_rendered_as_an_attributed_quotation_never_as_our_voice():
    app = _app()
    app.add_note(
        "oakfield",
        {"occurred_on": "2026-09-18", "at_time": "12:10", "author": "Anna", "text": ACCUSATION},
    )

    notes = app.notes_for_day("oakfield", date(2026, 9, 18))

    assert len(notes) == 1
    assert notes[0]["quote"] == ACCUSATION
    assert notes[0]["author"] == "Anna"
    assert notes[0]["lead_in"] == "Added by Anna at 12:10:"
    assert notes[0]["written_by"] == phrasing.NOTE_SOURCE_PERSON


def test_the_quotation_and_the_lead_in_are_separate_so_nothing_can_concatenate_them():
    """Returned as parts, not one string, so no caller can accidentally paste a
    household's words into a sentence the app appears to be making itself."""
    attributed = phrasing.attribute_note(ACCUSATION, "Anna", "12:10")

    assert set(attributed) == {"lead_in", "quote", "written_by", "author"}
    assert ACCUSATION not in attributed["lead_in"]


def test_an_unsigned_note_is_still_attributed_to_somebody():
    attributed = phrasing.attribute_note("Back door was open.", "  ", "12:10")

    assert attributed["author"] == "someone in the household"


def test_a_note_has_to_say_who_wrote_it():
    app = _app()

    with pytest.raises(ValueError, match="who wrote it"):
        app.add_note("oakfield", {"occurred_on": "2026-09-18", "text": "Something happened."})


def test_an_empty_note_is_rejected():
    app = _app()

    with pytest.raises(ValueError, match="something written in it"):
        app.add_note("oakfield", {"occurred_on": "2026-09-18", "author": "Anna", "text": "   "})


def test_a_note_time_that_is_not_a_time_is_rejected():
    app = _app()

    with pytest.raises(ValueError):
        app.add_note(
            "oakfield",
            {"occurred_on": "2026-09-18", "author": "Anna", "text": "x", "at_time": "lunchtime"},
        )


# --- rule 3: a note never changes a visit's status ---------------------


def test_a_note_does_not_change_what_the_door_recorded():
    """The strongest assertion in this file. The household can say a visit
    happened; the machine record still says, accurately, that the doorbell
    recorded nothing. Both are on the page. Neither overwrites the other."""
    app = _app()
    before = app.schedule_comparisons("oakfield", date(2026, 9, 18))

    app.add_note(
        "oakfield",
        {
            "occurred_on": "2026-09-18",
            "author": "Anna",
            "visit_label": "Midday check-in",
            "text": "She came at about ten past, I let her in through the back.",
        },
    )
    after = app.schedule_comparisons("oakfield", date(2026, 9, 18))

    assert before == after
    assert after[0]["status"] == phrasing.NO_ARRIVAL_RECORDED


def test_the_comparison_function_cannot_see_notes_even_if_it_wanted_to():
    """Structural, not a convention: compare_visit takes a visit, a log and a
    history floor, and schedule.py never imports the store the notes live in."""
    import inspect

    from threshold import schedule

    assert set(inspect.signature(schedule.compare_visit).parameters) == {
        "visit",
        "log",
        "history_available_since",
        "grace",
    }
    source = inspect.getsource(schedule)
    for forbidden in ("notes_for_day", "household_notes", "add_note", "from .store"):
        assert forbidden not in source


# --- rule 4: a note never reaches the model ----------------------------


def test_a_note_never_reaches_the_bedrock_prompt():
    """Letting a model read a household's accusation would launder it into the
    app's own voice, which is the exact harm the phrasing rules exist for."""
    app = _app()
    app.add_note(
        "oakfield", {"occurred_on": "2026-09-14", "author": "Anna", "text": ACCUSATION}
    )

    digest = app._digest_for(app.household("oakfield"), date(2026, 9, 14))
    facts = digest.facts_block()

    assert "carer" not in facts.lower()
    assert "Anna" not in facts
    assert ACCUSATION not in facts


def test_the_digest_builder_takes_no_notes_argument_at_all():
    import inspect

    params = inspect.signature(digest_mod.build).parameters
    assert "notes" not in params


def test_the_weekly_summary_is_unchanged_by_adding_a_note(monkeypatch):
    monkeypatch.setenv("THRESHOLD_DISABLE_BEDROCK", "1")
    app = _app()
    before = app.weekly_summary("oakfield", date(2026, 9, 14), refresh=True)["text"]

    app.add_note(
        "oakfield", {"occurred_on": "2026-09-14", "author": "Anna", "text": ACCUSATION}
    )
    after = app.weekly_summary("oakfield", date(2026, 9, 14), refresh=True)["text"]

    assert before == after


def test_a_note_is_not_run_through_the_forbidden_phrase_list():
    """Deliberate, and the reason is in phrasing.py: this is a person writing
    about her own mother's house, not the app making a claim."""
    app = _app()

    result = app.add_note(
        "oakfield", {"occurred_on": "2026-09-18", "author": "Anna", "text": ACCUSATION}
    )

    assert result["note_id"] > 0
    assert app.notes_for_day("oakfield", date(2026, 9, 18))[0]["quote"] == ACCUSATION


# --- ordering and scoping ---------------------------------------------


def test_notes_come_back_in_time_order_with_untimed_ones_last():
    app = _app()
    app.add_note(
        "oakfield",
        {"occurred_on": "2026-09-18", "author": "Anna", "text": "No time on this one."},
    )
    app.add_note(
        "oakfield",
        {"occurred_on": "2026-09-18", "at_time": "09:00", "author": "Anna", "text": "Early."},
    )

    quotes = [n["quote"] for n in app.notes_for_day("oakfield", date(2026, 9, 18))]

    assert quotes == ["Early.", "No time on this one."]


def test_notes_belong_to_one_day_and_do_not_leak_into_another():
    app = _app()
    app.add_note(
        "oakfield", {"occurred_on": "2026-09-18", "author": "Anna", "text": "Friday."}
    )

    assert app.notes_for_day("oakfield", date(2026, 9, 17)) == []
    assert len(app.notes_for_day("oakfield", date(2026, 9, 18))) == 1
