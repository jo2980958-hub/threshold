from datetime import datetime, timezone

from threshold import phrasing
from threshold.events import parse_webhook_event
from threshold.log import ArrivalLog
from threshold.schedule import CareVisit, compare_visit


def _dt(hour, minute=0, day=22):
    return datetime(2026, 9, day, hour, minute, tzinfo=timezone.utc)


def _event(request_id, hour, minute=0, sub_type="human", event_type="motion_detected"):
    return parse_webhook_event(
        {
            "request_id": request_id,
            "device_id": "front_door_1",
            "event_type": event_type,
            "created_at": f"2026-09-22T{hour:02d}:{minute:02d}:00Z",
            "attributes": {"sub_type": sub_type} if sub_type else {},
        }
    )


def test_activity_within_window_is_recorded():
    log = ArrivalLog()
    log.record(_event("evt-1", 8, 5))
    visit = CareVisit("Morning visit", _dt(8, 0), _dt(8, 45))

    result = compare_visit(visit, log, history_available_since=_dt(0))

    assert result.status_text == phrasing.ARRIVAL_RECORDED
    assert result.activity_recorded is True
    assert result.door_event_count == 1


def test_no_events_in_window_says_no_arrival_was_recorded_not_an_accusation():
    """This is the single most important assertion in this app. The product
    may only ever say an arrival was not recorded, never that a named person
    did not show up: the doorbell sees a door, not a visit, and a carer using
    a key safe or a back door produces exactly this signature."""
    log = ArrivalLog()  # nothing recorded at all
    visit = CareVisit("Midday check-in", _dt(12, 0), _dt(12, 30))

    result = compare_visit(visit, log, history_available_since=_dt(0))

    assert result.status_text == phrasing.NO_ARRIVAL_RECORDED
    assert result.activity_recorded is False
    assert "carer" not in result.status_text.lower()
    assert "did not" not in result.status_text.lower()


def test_events_outside_window_do_not_count_as_recorded():
    log = ArrivalLog()
    log.record(_event("evt-1", 6, 0))  # two hours before the visit
    visit = CareVisit("Morning visit", _dt(8, 0), _dt(8, 45))

    result = compare_visit(visit, log, history_available_since=_dt(0))

    assert result.status_text == phrasing.NO_ARRIVAL_RECORDED


def test_grace_period_covers_events_just_before_and_after_the_window():
    from datetime import timedelta

    log = ArrivalLog()
    log.record(_event("evt-1", 7, 57))  # 3 minutes early, inside default 5-min grace
    visit = CareVisit("Morning visit", _dt(8, 0), _dt(8, 45))

    result = compare_visit(visit, log, history_available_since=_dt(0), grace=timedelta(minutes=5))

    assert result.status_text == phrasing.ARRIVAL_RECORDED


def test_insufficient_history_produces_not_enough_history_status():
    log = ArrivalLog()
    visit = CareVisit("Morning visit", _dt(8, 0), _dt(8, 45))

    # history_available_since is AFTER the visit started: consent was granted
    # too late to have covered this window, so we must not claim "no arrival."
    result = compare_visit(visit, log, history_available_since=_dt(9, 0))

    assert result.status_text == phrasing.NOT_ENOUGH_HISTORY


def test_no_history_at_all_also_produces_not_enough_history_status():
    log = ArrivalLog()
    visit = CareVisit("Morning visit", _dt(8, 0), _dt(8, 45))

    result = compare_visit(visit, log, history_available_since=None)

    assert result.status_text == phrasing.NOT_ENOUGH_HISTORY


def test_wheelchair_misclassified_event_still_registers_as_recorded():
    """Same defect as test_events.py, exercised through the schedule
    comparison this time: sub_type=package must not cause a real arrival to
    read as 'no arrival was recorded.'"""
    log = ArrivalLog()
    log.record(_event("evt-1", 15, 10, sub_type="package"))
    visit = CareVisit("Afternoon visit", _dt(15, 0), _dt(15, 30))

    result = compare_visit(visit, log, history_available_since=_dt(0))

    assert result.status_text == phrasing.ARRIVAL_RECORDED


def test_every_possible_schedule_outcome_passes_phrasing_assert_safe():
    """Belt and braces: compare_visit already runs assert_safe internally, but
    re-check every producible status string directly against the forbidden list."""
    for status in (phrasing.ARRIVAL_RECORDED, phrasing.NO_ARRIVAL_RECORDED, phrasing.NOT_ENOUGH_HISTORY):
        phrasing.assert_safe(status)  # should not raise


def test_minutes_with_activity_spans_first_to_last_event_in_the_window():
    log = ArrivalLog()
    log.record(_event("evt-1", 8, 5))
    log.record(_event("evt-2", 8, 42))
    visit = CareVisit("Morning visit", _dt(8, 0), _dt(8, 45))

    result = compare_visit(visit, log, history_available_since=_dt(0))

    assert result.minutes_with_activity == 37


def test_minutes_with_activity_is_zero_for_a_single_event_not_none():
    """A single arrival with no departure event spans no time. Reporting that
    as zero rather than as the booked length is the honest answer: the door saw
    one moment, not a duration."""
    log = ArrivalLog()
    log.record(_event("evt-1", 8, 5))
    visit = CareVisit("Morning visit", _dt(8, 0), _dt(8, 45))

    assert compare_visit(visit, log, history_available_since=_dt(0)).minutes_with_activity == 0


def test_minutes_with_activity_is_none_when_nothing_was_recorded():
    log = ArrivalLog()
    visit = CareVisit("Morning visit", _dt(8, 0), _dt(8, 45))

    assert compare_visit(visit, log, history_available_since=_dt(0)).minutes_with_activity is None
