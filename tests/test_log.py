from datetime import datetime, timezone

from threshold.events import parse_webhook_event
from threshold.log import ArrivalLog


def _event(request_id, event_type="motion_detected", created_at="2026-09-22T08:00:00Z", sub_type="human"):
    return parse_webhook_event(
        {
            "request_id": request_id,
            "device_id": "front_door_1",
            "event_type": event_type,
            "created_at": created_at,
            "attributes": {"sub_type": sub_type} if sub_type else {},
        }
    )


def test_record_adds_new_door_activity():
    log = ArrivalLog()
    assert log.record(_event("evt-1")) is True
    assert len(log) == 1


def test_record_rejects_duplicate_request_id():
    log = ArrivalLog()
    log.record(_event("evt-1"))
    assert log.record(_event("evt-1")) is False
    assert len(log) == 1


def test_record_ignores_non_door_activity_event_types():
    log = ArrivalLog()
    event = _event("evt-1", event_type="device_online", sub_type=None)
    assert log.record(event) is False
    assert len(log) == 0


def test_events_between_filters_by_window():
    log = ArrivalLog()
    log.record(_event("evt-1", created_at="2026-09-22T07:00:00Z"))
    log.record(_event("evt-2", created_at="2026-09-22T08:10:00Z"))
    log.record(_event("evt-3", created_at="2026-09-22T20:00:00Z"))

    start = datetime(2026, 9, 22, 8, 0, tzinfo=timezone.utc)
    end = datetime(2026, 9, 22, 8, 30, tzinfo=timezone.utc)
    matches = log.events_between(start, end)

    assert [e.request_id for e in matches] == ["evt-2"]


def test_events_between_returns_sorted_order():
    log = ArrivalLog()
    log.record(_event("evt-late", created_at="2026-09-22T08:20:00Z"))
    log.record(_event("evt-early", created_at="2026-09-22T08:05:00Z"))

    start = datetime(2026, 9, 22, 8, 0, tzinfo=timezone.utc)
    end = datetime(2026, 9, 22, 8, 30, tzinfo=timezone.utc)
    matches = log.events_between(start, end)

    assert [e.request_id for e in matches] == ["evt-early", "evt-late"]


def test_last_event_before_returns_none_when_empty():
    log = ArrivalLog()
    when = datetime(2026, 9, 22, 9, 0, tzinfo=timezone.utc)
    assert log.last_event_before(when) is None


def test_last_event_before_returns_most_recent_prior_event():
    log = ArrivalLog()
    log.record(_event("evt-1", created_at="2026-09-22T07:00:00Z"))
    log.record(_event("evt-2", created_at="2026-09-22T08:00:00Z"))
    log.record(_event("evt-3", created_at="2026-09-22T10:00:00Z"))  # after `when`

    when = datetime(2026, 9, 22, 9, 0, tzinfo=timezone.utc)
    latest = log.last_event_before(when)

    assert latest.request_id == "evt-2"
