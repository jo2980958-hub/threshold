from datetime import datetime, timezone

from threshold.events import parse_webhook_event
from threshold.log import ArrivalLog
from threshold.store import EventStore


def _event(request_id, device_id="front_door_1", hour=8):
    return parse_webhook_event(
        {
            "request_id": request_id,
            "device_id": device_id,
            "event_type": "motion_detected",
            "created_at": f"2026-09-22T{hour:02d}:00:00Z",
            "attributes": {"sub_type": "human"},
        }
    )


def test_save_and_load_into_round_trips():
    store = EventStore(":memory:")
    store.save(_event("evt-1"))
    store.save(_event("evt-2", hour=9))

    log = ArrivalLog()
    loaded = store.load_into(log)

    assert loaded == 2
    assert len(log) == 2


def test_load_into_filters_by_device_id():
    store = EventStore(":memory:")
    store.save(_event("evt-1", device_id="front_door_1"))
    store.save(_event("evt-2", device_id="front_door_2"))

    log = ArrivalLog()
    store.load_into(log, device_id="front_door_1")

    assert len(log) == 1


def test_earliest_event_at_returns_none_when_no_events():
    store = EventStore(":memory:")
    assert store.earliest_event_at("front_door_1") is None


def test_earliest_event_at_returns_the_minimum_timestamp():
    store = EventStore(":memory:")
    store.save(_event("evt-late", hour=12))
    store.save(_event("evt-early", hour=7))

    earliest = store.earliest_event_at("front_door_1")

    assert earliest == datetime(2026, 9, 22, 7, 0, tzinfo=timezone.utc)


def test_duplicate_request_id_is_ignored_on_save():
    store = EventStore(":memory:")
    store.save(_event("evt-1"))
    store.save(_event("evt-1"))  # same request_id, should be a no-op

    log = ArrivalLog()
    loaded = store.load_into(log)

    assert loaded == 1
