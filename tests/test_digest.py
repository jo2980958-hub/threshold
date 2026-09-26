"""The weekly digest. Every number the family summary can contain starts here."""

from datetime import date, datetime, timezone

from threshold import digest as digest_mod
from threshold import phrasing
from threshold.events import parse_webhook_event
from threshold.log import ArrivalLog
from threshold.schedule import ScheduleRule, parse_clock

WEEK = date(2026, 9, 14)  # a Monday


def _event(request_id, day, hour, minute=0, device="front_door_1"):
    return parse_webhook_event(
        {
            "request_id": request_id,
            "device_id": device,
            "event_type": "motion_detected",
            "created_at": f"2026-09-{day:02d}T{hour:02d}:{minute:02d}:00Z",
            "attributes": {"sub_type": "human"},
        }
    )


def _log(*events):
    log = ArrivalLog()
    for event in events:
        log.record(event)
    return log


def _morning_rule():
    return ScheduleRule(
        label="Morning visit",
        days_of_week=(0, 1, 2, 3, 4),
        start_time=parse_clock("08:00"),
        end_time=parse_clock("08:45"),
    )


def _build(log, rules=(), since=datetime(2026, 9, 1, tzinfo=timezone.utc)):
    return digest_mod.build(
        household_name="Oakfield Road",
        log=log,
        rules=list(rules),
        week_start=WEEK,
        history_available_since=since,
        door_count=2,
    )


def test_week_start_for_snaps_any_day_to_its_monday():
    assert digest_mod.week_start_for(date(2026, 9, 17)) == WEEK
    assert digest_mod.week_start_for(date(2026, 9, 20)) == WEEK  # Sunday
    assert digest_mod.week_start_for(WEEK) == WEEK


def test_the_digest_covers_exactly_seven_days():
    digest = _build(_log())

    assert digest.days_covered == 7
    assert digest.week_end == date(2026, 9, 20)


def test_events_outside_the_week_are_not_counted():
    digest = _build(_log(_event("in", 15, 9), _event("out", 22, 9)))

    assert digest.total_events == 1


def test_quiet_days_are_the_days_with_nothing_at_any_door():
    digest = _build(_log(_event("a", 14, 9), _event("b", 16, 9, device="back_door_1")))

    assert len(digest.quiet_days) == 5
    assert date(2026, 9, 15) in digest.quiet_days
    assert date(2026, 9, 14) not in digest.quiet_days


def test_the_busiest_day_is_the_one_with_the_most_events():
    digest = _build(
        _log(_event("a", 14, 9), _event("b", 16, 9), _event("c", 16, 10), _event("d", 16, 11))
    )

    assert digest.busiest_day == date(2026, 9, 16)


def test_a_week_with_nothing_in_it_has_no_busiest_day_rather_than_a_wrong_one():
    digest = _build(_log())

    assert digest.busiest_day is None
    assert digest.earliest_activity_hour is None
    assert digest.total_events == 0


def test_visit_outcomes_are_counted_from_the_same_comparison_the_day_view_uses():
    # Activity on Monday and Tuesday mornings, nothing the rest of the week.
    digest = _build(_log(_event("a", 14, 8, 5), _event("b", 15, 8, 5)), rules=[_morning_rule()])

    assert digest.visits_scheduled == 5  # weekdays only
    assert digest.visits_with_activity == 2
    assert digest.visits_without_recorded_arrival == 3
    assert digest.visits_not_comparable == 0


def test_visits_before_the_history_starts_are_not_comparable_not_missing():
    """Ring does not back-fill. A visit booked before the account was linked
    must never be counted as one where no arrival was recorded."""
    digest = _build(
        _log(_event("a", 18, 8, 5)),
        rules=[_morning_rule()],
        since=datetime(2026, 9, 17, tzinfo=timezone.utc),
    )

    assert digest.visits_not_comparable == 3  # Mon, Tue, Wed
    assert digest.visits_with_activity == 1  # Fri
    assert digest.visits_without_recorded_arrival == 1  # Thu


def test_the_facts_block_is_plain_lines_with_no_raw_event_data_in_it():
    digest = _build(_log(_event("a", 14, 8, 5)), rules=[_morning_rule()])

    facts = digest.facts_block()

    assert "Oakfield Road" in facts
    assert "request_id" not in facts
    assert "front_door_1" not in facts
    assert "sub_type" not in facts


def test_allowed_numbers_contains_every_figure_the_facts_block_states():
    digest = _build(_log(_event("a", 14, 8, 5), _event("b", 14, 9, 5)), rules=[_morning_rule()])

    allowed = digest.allowed_numbers()

    assert str(digest.total_events) in allowed
    assert str(digest.visits_scheduled) in allowed
    assert "2026" in allowed


def test_allowed_numbers_does_not_quietly_contain_a_figure_nobody_computed():
    digest = _build(_log(_event("a", 14, 8, 5)), rules=[_morning_rule()])

    assert "999" not in digest.allowed_numbers()


def test_the_deterministic_summary_states_the_real_totals():
    digest = _build(_log(_event("a", 14, 8, 5), _event("b", 15, 8, 5)), rules=[_morning_rule()])

    text = digest_mod.deterministic_summary(digest)

    assert "2 events" in text
    assert "Of 5 booked care visits" in text
    assert "2 had front-door activity" in text


def test_the_deterministic_summary_passes_the_phrasing_gate():
    digest = _build(_log(_event("a", 14, 8, 5)), rules=[_morning_rule()])

    phrasing.assert_safe(digest_mod.deterministic_summary(digest))


def test_the_deterministic_summary_says_so_when_no_visits_are_booked():
    text = digest_mod.deterministic_summary(_build(_log(_event("a", 14, 9))))

    assert "No care visits were booked" in text


def test_as_dict_is_json_ready_for_the_facts_panel():
    payload = _build(_log(_event("a", 14, 8, 5)), rules=[_morning_rule()]).as_dict()

    assert payload["week_start"] == "2026-09-14"
    assert payload["door_count"] == 2
    assert len(payload["per_day"]) == 7
    assert all(isinstance(d["day"], str) for d in payload["per_day"])
