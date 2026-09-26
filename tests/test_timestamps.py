"""Clock changes, printed offsets, and the provenance word on every row.

Two things are tested here and both are about the same failure. A booked window
that drifts by an hour, or a timestamp that cannot be reconciled six months
later, produces a "no arrival was recorded" row against a visit that happened
exactly on time. In this product that row is a false accusation against a named,
low-paid person, so it is worth a test suite of its own rather than a discovery
in March.
"""

from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

import pytest

from threshold import phrasing
from threshold.events import parse_webhook_event
from threshold.household import Door, Household, validate_timezone
from threshold.log import ArrivalLog
from threshold.schedule import ScheduleRule, compare_visit, parse_clock, visits_for_date

LONDON = ZoneInfo("Europe/London")
LA = ZoneInfo("America/Los_Angeles")

# British Summer Time began on Sunday 29 March 2026. The Friday before is GMT,
# the Monday after is BST, and the booked time on the wall did not move.
FRIDAY_BEFORE = date(2026, 3, 27)
MONDAY_AFTER = date(2026, 3, 30)


def _morning_rule():
    return ScheduleRule(
        label="Morning visit",
        days_of_week=(0, 1, 2, 3, 4),
        start_time=parse_clock("08:00"),
        end_time=parse_clock("08:45"),
        active_from=date(2026, 1, 1),
    )


def _event_at(request_id: str, utc_iso: str):
    return parse_webhook_event(
        {
            "request_id": request_id,
            "device_id": "front_door_1",
            "event_type": "motion_detected",
            "created_at": utc_iso,
            "attributes": {"sub_type": "human"},
        }
    )


def _log(*events):
    log = ArrivalLog()
    for event in events:
        log.record(event)
    return log


# --- the clock change -------------------------------------------------


def test_a_booked_window_follows_the_household_wall_clock_across_a_clock_change():
    rule = _morning_rule()

    before = rule.visit_for(FRIDAY_BEFORE, LONDON)
    after = rule.visit_for(MONDAY_AFTER, LONDON)

    # Same 08:00 on the wall both days.
    assert before.scheduled_start.strftime("%H:%M") == "08:00"
    assert after.scheduled_start.strftime("%H:%M") == "08:00"
    # Different moments in UTC, because the clock moved.
    assert before.scheduled_start.utcoffset().total_seconds() == 0
    assert after.scheduled_start.utcoffset().total_seconds() == 3600


def test_a_visit_on_time_after_the_clock_change_is_not_reported_as_a_missing_arrival():
    """The regression this file exists for.

    A carer arrives at 08:05 on the Monday after the spring change. That is
    07:05 UTC. Expanding the booking against UTC instead of the household's own
    clock puts the window at 08:00-08:45 UTC, the event falls an hour outside
    it, and the app reports no arrival for a visit that happened on time.
    """
    log = _log(_event_at("evt-1", "2026-03-30T07:05:00Z"))
    since = datetime(2026, 1, 1, tzinfo=timezone.utc)

    correct = compare_visit(_morning_rule().visit_for(MONDAY_AFTER, LONDON), log, since)
    naive = compare_visit(_morning_rule().visit_for(MONDAY_AFTER, timezone.utc), log, since)

    assert correct.status_text == phrasing.ARRIVAL_RECORDED
    # And this is the bug, demonstrated rather than described.
    assert naive.status_text == phrasing.NO_ARRIVAL_RECORDED


def test_the_same_holds_for_the_autumn_change_in_the_other_direction():
    # British Summer Time ended on Sunday 25 October 2026.
    monday_after = date(2026, 10, 26)
    log = _log(_event_at("evt-1", "2026-10-26T08:05:00Z"))
    since = datetime(2026, 1, 1, tzinfo=timezone.utc)

    correct = compare_visit(_morning_rule().visit_for(monday_after, LONDON), log, since)

    assert correct.status_text == phrasing.ARRIVAL_RECORDED


def test_a_household_day_is_its_own_day_not_greenwich_s():
    rule = _morning_rule()

    la_visit = rule.visit_for(date(2026, 3, 30), LA)

    assert la_visit.scheduled_start.strftime("%H:%M") == "08:00"
    assert la_visit.scheduled_start.astimezone(timezone.utc).strftime("%H:%M") == "15:00"


def test_visits_for_date_passes_the_zone_down_to_every_rule():
    visits = visits_for_date([_morning_rule()], MONDAY_AFTER, LONDON)

    assert len(visits) == 1
    assert visits[0].scheduled_start.utcoffset().total_seconds() == 3600


def test_a_household_falls_back_to_utc_rather_than_failing_on_an_unknown_zone():
    household = Household(id="x", name="Somewhere", timezone_name="Mars/Olympus")

    assert household.tz == timezone.utc


def test_a_known_zone_resolves():
    assert Household(id="x", name="y", timezone_name="Europe/London").tz == LONDON


@pytest.mark.parametrize("name", ["Europe/London", "America/Los_Angeles", "UTC"])
def test_validate_timezone_accepts_real_zones(name):
    assert validate_timezone(name) == name


@pytest.mark.parametrize("name", ["", "   ", "Mars/Olympus", None, "BST"])
def test_validate_timezone_rejects_anything_this_machine_cannot_resolve(name):
    with pytest.raises(ValueError):
        validate_timezone(name)


# --- the printed offset -----------------------------------------------


def test_a_printed_timestamp_carries_its_zone_name_and_its_offset():
    moment = datetime(2026, 3, 14, 19, 7, 32, tzinfo=LA)

    assert phrasing.stamp(moment) == "2026-03-14 19:07:32 PDT (UTC-07:00)"


def test_the_offset_is_the_one_in_force_on_that_date_not_today_s():
    winter = phrasing.stamp(datetime(2026, 1, 14, 19, 7, 32, tzinfo=LA))
    summer = phrasing.stamp(datetime(2026, 7, 14, 19, 7, 32, tzinfo=LA))

    assert "PST (UTC-08:00)" in winter
    assert "PDT (UTC-07:00)" in summer


def test_a_moment_is_converted_into_the_household_zone_before_it_is_printed():
    moment = datetime(2026, 3, 30, 7, 5, tzinfo=timezone.utc)

    assert phrasing.stamp(moment, LONDON).startswith("2026-03-30 08:05:00 BST")


def test_a_timestamp_with_no_zone_at_all_says_so_the_way_rfc_3339_says_to():
    """-00:00 means the offset is unknown. +00:00 would assert UTC, which is a
    claim nobody made."""
    naive = datetime(2026, 3, 14, 19, 7, 32)

    printed = phrasing.stamp(naive)

    assert printed.endswith(f"(UTC{phrasing.UNKNOWN_OFFSET})")
    assert "+00:00" not in printed


def test_utc_prints_a_plus_offset_because_that_one_is_a_real_claim():
    printed = phrasing.stamp(datetime(2026, 3, 14, 19, 7, 32, tzinfo=timezone.utc))

    assert "(UTC+00:00)" in printed


# --- provenance, as a word ---------------------------------------------


def test_the_three_provenance_words_are_words_not_colours():
    for marker in (
        phrasing.PROVENANCE_RECORDED,
        phrasing.PROVENANCE_REPORTED,
        phrasing.PROVENANCE_INFERRED,
    ):
        assert marker.isupper()
        assert marker in phrasing.PROVENANCE_LEGEND


def test_every_provenance_word_has_a_plain_english_explanation():
    for marker, explanation in phrasing.PROVENANCE_LEGEND.items():
        assert len(explanation) > 20
        phrasing.assert_safe(explanation)


def test_ring_s_own_classification_is_reported_and_never_recorded():
    """A doorbell firing is recorded. A doorbell deciding the shape was a person
    is somebody's opinion, and this one has been documented calling a motorised
    wheelchair user a package."""
    from threshold.app import ThresholdApp

    app = ThresholdApp(webhook_secret="s")
    household = Household(
        id="h",
        name="Somewhere",
        timezone_name="Europe/London",
        doors=(Door("front_door_1", "h", "Front door", is_primary=True),),
    )
    app.store.upsert_household(household)

    classified = app._event_row(household, _event_at("a", "2026-03-30T07:05:00Z"))

    assert classified["provenance"] == phrasing.PROVENANCE_RECORDED
    assert classified["label_provenance"] == phrasing.PROVENANCE_REPORTED


def test_an_event_with_no_classification_reports_nothing_second_hand():
    from threshold.app import ThresholdApp

    app = ThresholdApp(webhook_secret="s")
    household = Household(
        id="h", name="Somewhere", doors=(Door("front_door_1", "h", "Front door"),)
    )
    app.store.upsert_household(household)
    press = parse_webhook_event(
        {
            "request_id": "b",
            "device_id": "front_door_1",
            "event_type": "button_press",
            "created_at": "2026-03-30T07:05:00Z",
            "attributes": {},
        }
    )

    row = app._event_row(household, press)

    assert row["label_provenance"] == phrasing.PROVENANCE_RECORDED


def test_a_row_carries_a_stamp_with_the_household_offset_in_it():
    from threshold.app import ThresholdApp

    app = ThresholdApp(webhook_secret="s")
    household = Household(
        id="h",
        name="Somewhere",
        timezone_name="Europe/London",
        doors=(Door("front_door_1", "h", "Front door"),),
    )
    app.store.upsert_household(household)

    row = app._event_row(household, _event_at("a", "2026-03-30T07:05:00Z"))

    assert row["stamp"] == "2026-03-30 08:05:00 BST (UTC+01:00)"


def test_a_households_timezone_survives_a_round_trip_through_sqlite():
    from threshold.app import ThresholdApp

    app = ThresholdApp(webhook_secret="s")
    app.store.upsert_household(
        Household(id="h", name="Somewhere", timezone_name="America/Los_Angeles")
    )

    assert app.household("h").timezone_name == "America/Los_Angeles"
    assert app.household("h").tz == LA
