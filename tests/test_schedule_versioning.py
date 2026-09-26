"""Changing a care package must never rewrite what last month looked like.

An unversioned schedule is a false-accusation machine. Move Tuesday's visit from
08:00 to 10:00 today, and every past Tuesday is suddenly compared against a
10:00 window it was never booked for, so every one of them reports "no arrival
was recorded at the front door". Nobody typed an accusation and the phrasing
rules were never broken: the harm arrived as a data bug, aimed at a named,
low-paid person, and the family member reading it has no way to tell.

These tests are the guard. They are deliberately written as "what the family
sees on a past day", not "what the store contains".
"""

from datetime import date, datetime, timezone

import json

import pytest

from threshold import phrasing
from threshold.app import ThresholdApp
from threshold.household import Door, Household
from threshold.webhook import sign

# Tuesdays, in a week the household recorded.
LAST_TUESDAY = date(2026, 9, 15)
THIS_TUESDAY = date(2026, 9, 22)
CHANGE_DAY = date(2026, 9, 17)


def _app():
    app = ThresholdApp(webhook_secret="s")
    app.store.upsert_household(
        Household(
            id="oakfield",
            name="Oakfield Road",
            doors=(Door("front_door_1", "oakfield", "Front door", is_primary=True),),
        )
    )
    # The household's record starts on the 14th. Without an event before the
    # first booked window, every comparison would correctly report "not enough
    # history" instead of what this file is trying to test.
    stamps = ["2026-09-14T07:00:00Z"] + [f"{d.isoformat()}T08:05:00Z" for d in (LAST_TUESDAY, THIS_TUESDAY)]
    for index, created_at in enumerate(stamps):
        payload = {
            "request_id": f"evt-{index}",
            "device_id": "front_door_1",
            "event_type": "motion_detected",
            "created_at": created_at,
            "attributes": {"sub_type": "human"},
        }
        raw = json.dumps(payload).encode("utf-8")
        app.handle_webhook(raw, sign("s", raw))
    return app


def _book_morning(app, today, **overrides):
    payload = {
        "label": "Morning visit",
        "days_of_week": [1],  # Tuesdays
        "start_time": "08:00",
        "end_time": "08:45",
        "active_from": "2026-09-01",
    }
    payload.update(overrides)
    return app.add_schedule_rule("oakfield", payload, today=today)


def _status_on(app, day):
    comparisons = app.schedule_comparisons("oakfield", day)
    return [c["status"] for c in comparisons]


# --- the trap ---------------------------------------------------------


def test_amending_a_visit_time_does_not_manufacture_a_missing_arrival_last_tuesday():
    """The test this file exists for."""
    app = _app()
    created = _book_morning(app, today=date(2026, 9, 1))
    assert _status_on(app, LAST_TUESDAY) == [phrasing.ARRIVAL_RECORDED]

    # The package changes: from the 17th the morning call is at 10:00.
    app.amend_schedule_rule(
        "oakfield",
        {
            "rule_id": created["rule_id"],
            "start_time": "10:00",
            "end_time": "10:45",
            "effective_from": CHANGE_DAY.isoformat(),
        },
        today=CHANGE_DAY,
    )

    # Last Tuesday still compares against the 08:00 booking that was in force.
    assert _status_on(app, LAST_TUESDAY) == [phrasing.ARRIVAL_RECORDED]


def test_the_new_time_applies_from_the_change_onwards():
    app = _app()
    created = _book_morning(app, today=date(2026, 9, 1))

    app.amend_schedule_rule(
        "oakfield",
        {
            "rule_id": created["rule_id"],
            "start_time": "10:00",
            "end_time": "10:45",
            "effective_from": CHANGE_DAY.isoformat(),
        },
        today=CHANGE_DAY,
    )

    # This Tuesday is after the change, and the 08:05 event is outside 10:00.
    assert _status_on(app, THIS_TUESDAY) == [phrasing.NO_ARRIVAL_RECORDED]


def test_exactly_one_visit_is_booked_on_each_side_of_the_change_never_two():
    """The old rule and the new one must not both fire on the same day, or the
    family sees the same visit twice with two different answers."""
    app = _app()
    created = _book_morning(app, today=date(2026, 9, 1))
    app.amend_schedule_rule(
        "oakfield",
        {
            "rule_id": created["rule_id"],
            "start_time": "10:00",
            "end_time": "10:45",
            "effective_from": CHANGE_DAY.isoformat(),
        },
        today=CHANGE_DAY,
    )

    assert len(app.schedule_comparisons("oakfield", LAST_TUESDAY)) == 1
    assert len(app.schedule_comparisons("oakfield", THIS_TUESDAY)) == 1


def test_the_amended_rule_is_closed_rather_than_deleted():
    app = _app()
    created = _book_morning(app, today=date(2026, 9, 1))

    result = app.amend_schedule_rule(
        "oakfield",
        {"rule_id": created["rule_id"], "start_time": "10:00", "end_time": "10:45",
         "effective_from": CHANGE_DAY.isoformat()},
        today=CHANGE_DAY,
    )

    rules = {r.rule_id: r for r in app.store.schedule_rules("oakfield")}
    assert created["rule_id"] in rules  # still there
    assert rules[created["rule_id"]].active_until == date(2026, 9, 16)
    assert result["previous_rule_ends"] == "2026-09-16"


# --- the default that makes it safe -----------------------------------


def test_a_new_rule_starts_today_rather_than_reaching_backwards():
    """If active_from defaulted to "always", booking a Tuesday visit today would
    report a missing arrival against every Tuesday the household has recorded,
    for a visit that was never booked on any of them."""
    app = _app()

    app.add_schedule_rule(
        "oakfield",
        {"label": "New call", "days_of_week": [1], "start_time": "10:00", "end_time": "10:45"},
        today=date(2026, 9, 17),
    )

    assert _status_on(app, LAST_TUESDAY) == []  # not booked then, so nothing is claimed
    assert _status_on(app, THIS_TUESDAY) == [phrasing.NO_ARRIVAL_RECORDED]


def test_backdating_is_still_possible_but_only_by_asking_for_it():
    app = _app()

    app.add_schedule_rule(
        "oakfield",
        {
            "label": "Morning visit",
            "days_of_week": [1],
            "start_time": "08:00",
            "end_time": "08:45",
            "active_from": "2026-09-01",
        },
        today=date(2026, 9, 17),
    )

    assert _status_on(app, LAST_TUESDAY) == [phrasing.ARRIVAL_RECORDED]


def test_a_rule_that_ends_before_it_starts_is_rejected():
    app = _app()

    with pytest.raises(ValueError, match="cannot end before it starts"):
        app.add_schedule_rule(
            "oakfield",
            {
                "label": "Morning visit",
                "days_of_week": [1],
                "start_time": "08:00",
                "end_time": "08:45",
                "active_from": "2026-09-17",
                "active_until": "2026-09-01",
            },
        )


# --- ending a package --------------------------------------------------


def test_ending_a_package_leaves_the_days_it_governed_alone():
    app = _app()
    created = _book_morning(app, today=date(2026, 9, 1))

    app.end_schedule_rule(
        "oakfield", {"rule_id": created["rule_id"], "last_day": "2026-09-16"}
    )

    assert _status_on(app, LAST_TUESDAY) == [phrasing.ARRIVAL_RECORDED]
    assert _status_on(app, THIS_TUESDAY) == []


def test_ending_a_package_never_deletes_the_row():
    app = _app()
    created = _book_morning(app, today=date(2026, 9, 1))

    app.end_schedule_rule(
        "oakfield", {"rule_id": created["rule_id"], "last_day": "2026-09-16"}
    )

    assert any(r.rule_id == created["rule_id"] for r in app.store.schedule_rules("oakfield"))


def test_ending_a_rule_that_does_not_exist_is_an_error_not_a_silent_no_op():
    app = _app()

    with pytest.raises(ValueError, match="no schedule rule"):
        app.end_schedule_rule("oakfield", {"rule_id": 9999})


def test_amending_a_rule_that_does_not_exist_is_an_error():
    app = _app()

    with pytest.raises(ValueError, match="no schedule rule"):
        app.amend_schedule_rule("oakfield", {"rule_id": 9999, "start_time": "10:00"})


def test_a_rule_that_never_governed_a_day_can_be_removed_outright():
    """A visit booked for next week and cancelled before it starts destroys no
    history, so removing it is honest. Anything already in force is closed."""
    app = _app()
    created = app.add_schedule_rule(
        "oakfield",
        {
            "label": "Future call",
            "days_of_week": [1],
            "start_time": "10:00",
            "end_time": "10:45",
            "active_from": "2026-10-01",
        },
        today=date(2026, 9, 17),
    )

    removed = app.store.delete_unstarted_schedule_rule(
        "oakfield", created["rule_id"], date(2026, 9, 17)
    )

    assert removed is True
    assert app.store.schedule_rules("oakfield") == []


def test_a_rule_already_in_force_refuses_to_be_deleted_outright():
    app = _app()
    created = _book_morning(app, today=date(2026, 9, 1))

    removed = app.store.delete_unstarted_schedule_rule(
        "oakfield", created["rule_id"], date(2026, 9, 17)
    )

    assert removed is False
    assert len(app.store.schedule_rules("oakfield")) == 1


# --- the fixture ships the versioned case ------------------------------


def test_the_demo_fixture_carries_a_real_package_amendment():
    from pathlib import Path

    fixture = json.loads(
        (Path(__file__).parent.parent / "fixtures" / "households.json").read_text()
    )
    oakfield = next(h for h in fixture["households"] if h["id"] == "oakfield")
    evenings = [r for r in oakfield["schedule_rules"] if r["label"] == "Evening visit"]

    assert len(evenings) == 2
    closed = next(r for r in evenings if r.get("active_until"))
    opened = next(r for r in evenings if not r.get("active_until"))
    assert closed["active_until"] == "2026-09-16"
    assert opened["active_from"] == "2026-09-17"


def test_every_fixture_rule_carries_an_active_from():
    from pathlib import Path

    fixture = json.loads(
        (Path(__file__).parent.parent / "fixtures" / "households.json").read_text()
    )
    for household in fixture["households"]:
        for rule in household["schedule_rules"]:
            assert rule.get("active_from"), f"{household['id']}: {rule['label']}"
