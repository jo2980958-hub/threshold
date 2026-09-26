"""The silence watch: the surface for the household with no care package.

The assertions that matter here are the ones about restraint. A watch that
fires before it has learned anything, or that reaches for alarmed language,
gets muted by the family in week one and is worth nothing after that.
"""

from datetime import datetime, timedelta, timezone

from threshold import phrasing, silence
from threshold.events import parse_webhook_event
from threshold.log import ArrivalLog


def _dt(day, hour, minute=0):
    return datetime(2026, 9, day, hour, minute, tzinfo=timezone.utc)


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


def test_quiet_longer_than_the_threshold_raises_an_alert():
    log = _log(_event("evt-1", 20, 19, 40))

    state = silence.evaluate(
        log=log,
        now=_dt(22, 12),
        threshold_hours=12,
        history_available_since=_dt(14, 7),
    )

    assert state.is_alert is True
    assert "40 hours" in state.message
    assert str(12) in state.message


def test_an_alert_never_reaches_for_alarmed_language():
    log = _log(_event("evt-1", 20, 19, 40))

    state = silence.evaluate(
        log=log, now=_dt(22, 12), threshold_hours=12, history_available_since=_dt(14, 7)
    )

    lowered = state.message.lower()
    for word in ("emergency", "urgent", "fallen", "danger", "immediately", "call"):
        assert word not in lowered
    phrasing.assert_safe(state.message)


def test_recent_activity_is_within_the_pattern():
    log = _log(_event("evt-1", 22, 8, 30))

    state = silence.evaluate(
        log=log, now=_dt(22, 12), threshold_hours=12, history_available_since=_dt(14, 7)
    )

    assert state.state == silence.STATE_WITHIN_PATTERN
    assert state.is_alert is False


def test_a_freshly_linked_household_is_never_told_it_is_quiet():
    """Ring does not back-fill, so a household linked this morning knows nothing
    about its own rhythm. Saying "quiet" on day one would be inventing a
    baseline from zero days of data."""
    log = _log(_event("evt-1", 22, 8))

    state = silence.evaluate(
        log=log,
        now=_dt(22, 23),
        threshold_hours=12,
        history_available_since=_dt(22, 8),
    )

    assert state.state == silence.STATE_NOT_ENOUGH_HISTORY
    assert state.message == phrasing.SILENCE_NOT_ENOUGH_HISTORY


def test_no_history_at_all_is_not_enough_history():
    state = silence.evaluate(
        log=ArrivalLog(), now=_dt(22, 12), threshold_hours=12, history_available_since=None
    )

    assert state.state == silence.STATE_NOT_ENOUGH_HISTORY


def test_nothing_yet_today_past_the_usual_hour_alerts_before_the_hour_count_would():
    """The overnight gap eats most of a 12-hour threshold, so a quiet morning
    would otherwise not register until the afternoon. The usual-hour trigger is
    the one that catches it while it still matters."""
    log = _log(_event("evt-1", 21, 18))

    state = silence.evaluate(
        log=log,
        now=_dt(22, 11),
        threshold_hours=24,  # deliberately too long to fire on its own
        history_available_since=_dt(14, 7),
        usual_activity_by_hour=10,
    )

    assert state.is_alert is True
    assert state.message == phrasing.NO_MORNING_ACTIVITY_YET


def test_the_usual_hour_trigger_does_not_fire_before_that_hour():
    log = _log(_event("evt-1", 21, 18))

    state = silence.evaluate(
        log=log,
        now=_dt(22, 9),
        threshold_hours=24,
        history_available_since=_dt(14, 7),
        usual_activity_by_hour=10,
    )

    assert state.is_alert is False


def test_the_usual_hour_trigger_is_silent_once_something_happens_today():
    log = _log(_event("evt-1", 21, 18), _event("evt-2", 22, 7, 15))

    state = silence.evaluate(
        log=log,
        now=_dt(22, 11),
        threshold_hours=24,
        history_available_since=_dt(14, 7),
        usual_activity_by_hour=10,
    )

    assert state.is_alert is False


def test_activity_at_a_back_door_counts_so_a_back_door_day_is_not_a_silent_day():
    log = _log(_event("evt-1", 20, 19), _event("evt-2", 22, 9, device="back_door_1"))

    state = silence.evaluate(
        log=log, now=_dt(22, 12), threshold_hours=12, history_available_since=_dt(14, 7)
    )

    assert state.is_alert is False
    assert state.last_activity_at == _dt(22, 9)


def test_the_threshold_is_configurable_and_a_longer_one_does_not_fire():
    log = _log(_event("evt-1", 21, 20))

    quiet_hours = (_dt(22, 12) - _dt(21, 20)).total_seconds() / 3600
    assert quiet_hours == 16

    fires = silence.evaluate(
        log=log, now=_dt(22, 12), threshold_hours=12, history_available_since=_dt(14, 7)
    )
    holds = silence.evaluate(
        log=log, now=_dt(22, 12), threshold_hours=20, history_available_since=_dt(14, 7)
    )

    assert fires.is_alert is True
    assert holds.is_alert is False


def test_min_history_days_is_the_documented_two_days():
    log = _log(_event("evt-1", 20, 8))
    now = _dt(22, 12)

    just_short = silence.evaluate(
        log=log,
        now=now,
        threshold_hours=12,
        history_available_since=now - timedelta(days=silence.MIN_HISTORY_DAYS) + timedelta(minutes=1),
    )
    just_enough = silence.evaluate(
        log=log,
        now=now,
        threshold_hours=12,
        history_available_since=now - timedelta(days=silence.MIN_HISTORY_DAYS) - timedelta(minutes=1),
    )

    assert just_short.state == silence.STATE_NOT_ENOUGH_HISTORY
    assert just_enough.state == silence.STATE_ALERT


def test_as_dict_rounds_hours_to_whole_hours_for_display():
    log = _log(_event("evt-1", 20, 19, 40))

    payload = silence.evaluate(
        log=log, now=_dt(22, 12), threshold_hours=12, history_available_since=_dt(14, 7)
    ).as_dict()

    assert payload["state"] == silence.STATE_ALERT
    assert payload["threshold_hours"] == 12
    assert payload["hours_since_last_activity"] == 40
    assert payload["last_activity_at"].startswith("2026-09-20T19:40")


def test_the_hours_in_the_message_match_the_hours_in_the_payload():
    """"50 hours" beside "51.0" reads as a bug in a product whose whole claim is
    that the record is careful."""
    log = _log(_event("evt-1", 20, 18, 17))

    state = silence.evaluate(
        log=log, now=_dt(22, 21), threshold_hours=12, history_available_since=_dt(14, 7)
    )

    assert str(state.as_dict()["hours_since_last_activity"]) + " hours" in state.message
